use crate::{
    i18n::{I18n, Language},
    integrations::Tray,
    ui::{self, theme, widgets::*},
};
use cplus_core::{
    features::{self, home, popup_blocker, system_slim},
    jobs::{Context, Progress},
    *,
};
use cplus_platform_windows::{
    filesystem::{self, Disk},
    shell,
};
use iced::{
    Element, Length, Size, Subscription, Task,
    widget::{Space, button, column, container, row},
    window,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, Sender},
    },
    time::{Duration, Instant},
};

#[derive(Clone, Default)]
pub struct Config {
    pub taskbar_test: bool,
    pub preview: bool,
    pub data: Option<PathBuf>,
    pub language: Option<String>,
    pub page: Option<String>,
    pub background: bool,
    pub scheduled: bool,
    pub system_job: Option<PathBuf>,
    pub smoke: Option<PathBuf>,
    pub quit_after: Option<u64>,
}
impl Config {
    pub fn parse() -> anyhow::Result<Self> {
        let mut c = Self::default();
        let mut args = std::env::args().skip(1);
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--taskbar-test" => c.taskbar_test = true,
                "--preview" => c.preview = true,
                "--data-dir" => {
                    c.data = Some(
                        args.next()
                            .ok_or_else(|| anyhow::anyhow!("Missing data directory"))?
                            .into(),
                    )
                }
                "--language" => c.language = args.next(),
                "--page" => c.page = args.next(),
                "--background" => c.background = true,
                "--scheduled-cleanup" => c.scheduled = true,
                "--system-job" => c.system_job = args.next().map(PathBuf::from),
                "--smoke-test" => {
                    c.smoke = Some(
                        args.next()
                            .ok_or_else(|| anyhow::anyhow!("Missing smoke-test report path"))?
                            .into(),
                    )
                }
                "--quit-after" => c.quit_after = args.next().and_then(|s| s.parse().ok()),
                _ => anyhow::bail!("Unknown argument: {arg}"),
            }
        }
        if c.data.is_some() && !c.preview {
            anyhow::bail!("Custom data directories require --preview");
        }
        if c.taskbar_test && (!c.preview || c.data.is_none()) {
            anyhow::bail!("Taskbar tests require --preview and an isolated --data-dir");
        }
        if !c.preview {
            c.quit_after = None;
        }
        Ok(c)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Dialog {
    Settings,
    Activity,
    Feedback,
    Donate,
    About,
    Updates,
    History(Feature),
    Residue,
    Confirm,
    Information,
}
#[derive(Clone, Debug)]
pub enum Action {
    Delete,
    Move,
    Compress,
    Uninstall,
    Restore(String),
    System(system_slim::Action),
}
#[derive(Clone, Debug)]
pub struct Pending {
    pub feature: Feature,
    pub action: Action,
    pub ids: Vec<String>,
}
#[derive(Clone, Debug)]
pub enum Message {
    NavHover(Feature, bool),
    MonitorTab(crate::monitor::Tab),
    MonitorReady(window::Id, u64, i32, i32),
    MonitorClose,
    MonitorMain,
    MonitorCleanup,
    WindowFocused(window::Id),
    Tick,
    Opened(window::Id),
    Close(window::Id),
    TrayReady(window::Id, u64, i32, i32),
    TrayDismiss(window::Id),
    TrayAction(Box<Message>),
    Navigate(Feature),
    Menu,
    Dialog(Dialog),
    Dismiss(window::Id),
    Scan,
    Search(String),
    CancelLargeScan,
    DuplicateDrive(String),
    DuplicateMinimum(String),
    DuplicateSmartSelect,
    DuplicateSelect(Vec<String>),
    DuplicateExpandAll,
    DuplicateDelete(Vec<String>),
    DuplicateEntryPage(String, usize),
    Range(String),
    Toggle(String),
    Group(String),
    CategoryGroup(String),
    All(bool),
    Defaults,
    Expand(String),
    Sort(String),
    Request(Action, Option<String>),
    Confirm,
    Language(Language),
    Setting(&'static str, bool),
    Time(String),
    SaveSettings,
    Folder(Feature),
    FolderForChat(String),
    ResetChat(String),
    Open(String),
    Reveal(PathBuf),
    HistoryRestore(Feature, String),
    PopupToggle(String, bool),
    DeleteActivity,
    ClearActivity,
    SelectActivity(String),
    SelectActivities(Vec<String>),
    ActivityAction(String),
    ActivityResult(Option<bool>),
    CopyEnvironment,
    Copy(String),
    DeleteResidues,
    Exit,
}
pub enum Event {
    Report(ScanResult),
    Finished(Result<JobValue, String>),
    Folder(Feature, Option<PathBuf>),
    ChatFolder(String, Option<PathBuf>),
    PopupRestored(Vec<popup_blocker::Group>),
    Disks(Vec<Disk>),
    Residue(ScanResult),
    SettingsSaved(Result<Settings, String>),
}
pub enum JobValue {
    Scan,
    Operation(Feature, OperationResult),
    History(Feature, Vec<serde_json::Value>),
    Release(cplus_core::updates::Release),
    System(String),
}
pub struct Job {
    pub ctx: Context,
    pub name: String,
}
pub struct App {
    hovered_nav: Option<Feature>,
    pub monitor: crate::monitor::Monitor,
    pub store: Store,
    pub config: Config,
    pub i18n: I18n,
    pub settings: Settings,
    pub edit: Settings,
    pub language: Language,
    pub page: Feature,
    pub reports: BTreeMap<Feature, Arc<ScanResult>>,
    pub selected: BTreeMap<Feature, BTreeSet<String>>,
    pub expanded: BTreeSet<String>,
    pub search: String,
    pub range: String,
    pub ranges: Vec<String>,
    pub days: u32,
    pub sort: String,
    pub descending: bool,
    pub disks: Vec<Disk>,
    pub destination_apps: PathBuf,
    pub destination_files: PathBuf,
    pub duplicate_drive: String,
    pub duplicate_minimum: usize,
    pub duplicate_keeper: features::duplicates::KeeperRule,
    pub duplicate_page: usize,
    pub duplicate_entry_pages: BTreeMap<String, usize>,
    pub windows: BTreeMap<window::Id, Dialog>,
    pub main: window::Id,
    pub menu: bool,
    pub pending: Option<Pending>,
    pub history: BTreeMap<Feature, Vec<serde_json::Value>>,
    pub activities: Vec<Activity>,
    pub activity_selected: BTreeSet<String>,
    pub activity_action: String,
    pub activity_result: Option<bool>,
    pub release: Option<cplus_core::updates::Release>,
    pub update_error: String,
    pub info: String,
    pub status: String,
    pub status_feature: Feature,
    pub status_elapsed: Option<u64>,
    pub scan_elapsed: BTreeMap<Feature, u64>,
    settings_saving: bool,
    settings_save_due: Option<Instant>,
    pub issues: Vec<Issue>,
    pub job: Option<Job>,
    pub progress: Progress,
    pub engine: Arc<Mutex<popup_blocker::Engine>>,
    pub residue: Option<Arc<ScanResult>>,
    pub residue_selected: BTreeSet<String>,
    pub tx: Sender<Event>,
    rx: Receiver<Event>,
    tray: Option<Tray>,
    tray_menu: Option<window::Id>,
    stop: Arc<AtomicBool>,
    quit: bool,
    started: Instant,
    pub component_analysis: String,
}
impl Drop for App {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(job) = &self.job {
            job.ctx.cancel.cancel();
        }
    }
}
pub fn feature_key(f: Feature) -> &'static str {
    match f {
        Feature::Home => "Home",
        Feature::Drive => "Drive Cleanup",
        Feature::Chat => "Chat Cleanup",
        Feature::LargeFiles => "Large File Mover",
        Feature::AppMover => "App Mover",
        Feature::Compression => "App Compression",
        Feature::Duplicates => "Duplicate Cleanup",
        Feature::Privacy => "Privacy Cleanup",
        Feature::Uninstall => "App Uninstaller",
        Feature::Popup => "Popup Blocker",
        Feature::System => "System Slimming",
    }
}
impl App {
    pub fn new(config: Config) -> (Self, Task<Message>) {
        let monitor_preview = config.preview && config.page.as_deref() == Some("monitor");
        let store = Store::new(config.data.clone(), config.preview)
            .expect("Cannot open application data folder");
        let _ = store.backup_legacy();
        let settings = store.settings();
        let code = config
            .language
            .as_deref()
            .unwrap_or(if settings.language.is_empty() {
                shell::user_language()
            } else {
                &settings.language
            });
        let language = Language::from_code(code);
        let i18n = I18n::new(language);
        let ranges = [
            "All time",
            "Within 7 days",
            "Within 30 days",
            "Within 180 days",
        ]
        .map(|s| i18n.t(s))
        .to_vec();
        let range = ranges[0].clone();
        let (tx, rx) = mpsc::channel();
        let main_settings = window::Settings {
            size: Size::new(1240.0, 850.0),
            min_size: Some(Size::new(1080.0, 740.0)),
            position: window::Position::Centered,
            visible: !config.background && !monitor_preview,
            exit_on_close_request: false,
            icon: window_icon(),
            ..Default::default()
        };
        let (main, open) = window::open(main_settings);
        let engine = Arc::new(Mutex::new(popup_blocker::Engine {
            groups: vec![],
            state: popup_blocker::State::default(),
            error: String::new(),
        }));
        let stop = Arc::new(AtomicBool::new(false));
        let engine_init = engine.clone();
        let initial_store = store.clone();
        let sender = tx.clone();
        let stop_init = stop.clone();
        std::thread::spawn(move || {
            let restored = popup_blocker::Engine::new(&initial_store);
            let groups = restored.groups.clone();
            *engine_init.lock().unwrap_or_else(|e| e.into_inner()) = restored;
            let _ = sender.send(Event::PopupRestored(groups));
            popup_blocker::background(initial_store, engine_init, stop_init);
            let _ = sender.send(Event::Disks(filesystem::disks()));
        });
        let tray = Tray::new().ok();
        let page = config
            .page
            .as_ref()
            .and_then(|s| Feature::ALL.into_iter().find(|f| f.key() == s))
            .unwrap_or(Feature::Home);
        let destination_apps = store.default_destination(true);
        let destination_files = store.default_destination(false);
        let activities = store.activities();
        let mut app = Self {
            hovered_nav: None,
            monitor: crate::monitor::Monitor::new(
                settings.taskbar_monitor || monitor_preview,
                config.preview && !config.taskbar_test,
            ),
            store,
            config,
            i18n,
            settings: settings.clone(),
            edit: settings,
            language,
            page,
            reports: BTreeMap::new(),
            selected: BTreeMap::new(),
            expanded: BTreeSet::new(),
            search: String::new(),
            range,
            ranges,
            days: 0,
            sort: "Date".into(),
            descending: true,
            disks: vec![],
            destination_apps,
            destination_files,
            duplicate_drive: cplus_core::common::system_drive().display().to_string(),
            duplicate_minimum: 1,
            duplicate_keeper: Default::default(),
            duplicate_page: 0,
            duplicate_entry_pages: BTreeMap::new(),
            windows: BTreeMap::new(),
            main,
            menu: false,
            pending: None,
            history: BTreeMap::new(),
            activities,
            activity_selected: BTreeSet::new(),
            activity_action: String::new(),
            activity_result: None,
            release: None,
            update_error: String::new(),
            info: String::new(),
            status: String::new(),
            status_feature: Feature::Home,
            scan_elapsed: BTreeMap::new(),
            status_elapsed: None,
            settings_saving: false,
            settings_save_due: None,
            issues: vec![],
            job: None,
            progress: Progress::default(),
            engine,
            residue: None,
            residue_selected: BTreeSet::new(),
            tx,
            rx,
            tray,
            tray_menu: None,
            stop,
            quit: false,
            started: Instant::now(),
            component_analysis: String::new(),
        };
        let categories = features::drive_cleanup::default_categories();
        let choices = categories
            .iter()
            .filter(|c| c.default_selected)
            .map(|c| c.id.clone())
            .collect();
        app.selected.insert(Feature::Drive, choices);
        app.reports.insert(
            Feature::Drive,
            Arc::new(ScanResult {
                feature: Feature::Drive,
                categories,
                ..Default::default()
            }),
        );
        let mut tasks = vec![open.map(Message::Opened)];
        if monitor_preview {
            tasks.push(app.open_monitor(crate::monitor::Tab::Overview, 1000, 900));
        }
        (app, Task::batch(tasks))
    }
    pub fn t(&self, s: &str) -> String {
        self.i18n.t(s)
    }
    pub fn name(&self, f: Feature) -> String {
        if f == Feature::Drive && self.i18n.language != Language::English {
            format!("{}{}", self.drive_letter(), self.t(feature_key(f)))
        } else {
            self.t(feature_key(f))
        }
    }
    pub fn drive_letter(&self) -> String {
        common::system_drive()
            .to_string_lossy()
            .chars()
            .next()
            .unwrap_or('C')
            .to_string()
    }
    pub fn drive(&self) -> Option<&Disk> {
        self.disks.iter().find(|d| {
            d.root
                .eq_ignore_ascii_case(&common::system_drive().to_string_lossy())
        })
    }
    pub fn drive_name(&self) -> String {
        if self.i18n.language == Language::English {
            format!("{} {}", self.drive_letter(), self.t("Drive"))
        } else {
            format!("{}{}", self.drive_letter(), self.t("Drive"))
        }
    }
    pub fn busy(&self) -> bool {
        self.job.is_some()
    }
    pub fn choices(&self, f: Feature) -> BTreeSet<String> {
        self.selected.get(&f).cloned().unwrap_or_default()
    }
    pub const DUPLICATE_MINIMUMS: [u64; 6] = [
        0,
        features::duplicates::DEFAULT_MINIMUM,
        1024 * 1024,
        10 * 1024 * 1024,
        100 * 1024 * 1024,
        500 * 1024 * 1024,
    ];
    pub fn duplicate_minimum_options(&self) -> Vec<String> {
        vec![
            self.t("All files"),
            "200 KB".into(),
            "1 MB".into(),
            "10 MB".into(),
            "100 MB".into(),
            "500 MB".into(),
        ]
    }
    fn invalidate_duplicates(&mut self) {
        self.reports.remove(&Feature::Duplicates);
        self.selected.remove(&Feature::Duplicates);
        self.scan_elapsed.remove(&Feature::Duplicates);
        self.duplicate_page = 0;
        self.duplicate_entry_pages.clear();
        if self
            .pending
            .as_ref()
            .is_some_and(|p| p.feature == Feature::Duplicates)
        {
            self.pending = None;
        }
        if self.status_feature == Feature::Duplicates {
            self.status.clear();
            self.status_elapsed = None;
        }
    }
    pub fn report(&self) -> Option<&ScanResult> {
        self.reports.get(&self.page).map(Arc::as_ref)
    }
    pub fn selected_size(&self, f: Feature) -> u64 {
        let selected = self.choices(f);
        let Some(r) = self.reports.get(&f) else {
            return 0;
        };
        if f == Feature::Drive {
            return r
                .categories
                .iter()
                .filter(|c| selected.contains(&c.id))
                .map(|c| c.bytes)
                .sum();
        }
        if !r.apps.is_empty() {
            r.apps
                .iter()
                .filter(|a| selected.contains(&a.id))
                .map(|a| {
                    if f == Feature::Compression {
                        a.physical
                    } else {
                        a.logical
                    }
                })
                .sum()
        } else {
            r.files
                .iter()
                .filter(|x| selected.contains(&x.id) || selected.contains(&x.category))
                .map(|x| {
                    if f == Feature::Duplicates {
                        x.allocated
                    } else {
                        x.size
                    }
                })
                .sum()
        }
    }
    pub fn metric(&self, f: Feature) -> String {
        self.reports
            .get(&f)
            .filter(|r| !r.token.is_empty() || f == Feature::Popup)
            .map(|r| {
                if f.byte_metric() {
                    bytes(r.bytes())
                } else {
                    format!(
                        "{} {}",
                        r.count(),
                        self.t(if f == Feature::Uninstall {
                            "apps"
                        } else if f == Feature::Privacy {
                            "entries"
                        } else {
                            "items"
                        })
                    )
                }
            })
            .unwrap_or_else(|| "—".into())
    }
    pub fn title(&self, id: window::Id) -> String {
        if self.monitor.window == Some(id) {
            return format!(
                "CPlus Cleaner · {}{}",
                self.t("System status"),
                if self.config.preview {
                    " · Preview"
                } else {
                    ""
                }
            );
        }
        if id == self.main {
            format!(
                "CPlus Cleaner{}",
                if self.config.preview {
                    " · Preview"
                } else {
                    ""
                }
            )
        } else {
            self.windows
                .get(&id)
                .map(|d| self.dialog_title(d))
                .unwrap_or_else(|| "CPlus Cleaner".into())
        }
    }
    pub fn dialog_title(&self, d: &Dialog) -> String {
        if *d == Dialog::Confirm
            && self
                .pending
                .as_ref()
                .is_some_and(|p| matches!(p.action, Action::Delete))
        {
            return self.t("Confirm deletion");
        }
        self.t(match d {
            Dialog::Settings => "Settings",
            Dialog::Activity => "Activity Log",
            Dialog::Feedback => "Feedback",
            Dialog::Donate => "Donate",
            Dialog::About => "About",
            Dialog::Updates => "Check for Updates",
            Dialog::History(f) => match f {
                Feature::AppMover => "Move History",
                Feature::Compression => "Compression History",
                Feature::LargeFiles => "Large File History",
                _ => "Blocking History",
            },
            Dialog::Residue => "Uninstall Residues",
            Dialog::Confirm => "Confirm",
            Dialog::Information => "Information",
        })
    }
    pub fn subscription(&self) -> Subscription<Message> {
        Subscription::batch([
            iced::time::every(Duration::from_millis(120)).map(|_| Message::Tick),
            window::close_requests().map(Message::Close),
            window::events().filter_map(|(id, event)| match event {
                window::Event::Focused => Some(Message::WindowFocused(id)),
                window::Event::Unfocused => Some(Message::TrayDismiss(id)),
                _ => None,
            }),
            iced::event::listen_with(|event, _, id| {
                matches!(
                    event,
                    iced::Event::Keyboard(iced::keyboard::Event::KeyPressed {
                        key: iced::keyboard::Key::Named(iced::keyboard::key::Named::Escape),
                        ..
                    })
                )
                .then_some(Message::TrayDismiss(id))
            }),
        ])
    }
    pub fn start(
        &mut self,
        feature: Feature,
        name: &str,
        run: impl FnOnce(Store, Context, Sender<Event>) -> anyhow::Result<JobValue> + Send + 'static,
    ) {
        if self.busy() {
            return;
        }
        let ctx = Context::new(feature);
        let worker = ctx.clone();
        self.progress = ctx.snapshot();
        self.job = Some(Job {
            ctx,
            name: name.into(),
        });
        self.status.clear();
        self.status_feature = feature;
        self.status_elapsed = None;
        self.issues.clear();
        let store = self.store.clone();
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                run(store, worker, tx.clone())
            }))
            .map_err(|_| "The background task stopped unexpectedly".into())
            .and_then(|r| r.map_err(|e| e.to_string()));
            let _ = tx.send(Event::Finished(result));
        });
    }
    fn pending_home_scans(&self) -> Vec<Feature> {
        Feature::HOME
            .into_iter()
            .filter(|f| {
                !self.scan_elapsed.contains_key(f)
                    || self.reports.get(f).is_none_or(|r| r.cancelled)
            })
            .collect()
    }
    pub fn scan(&mut self) {
        if self.busy() {
            return;
        }
        let feature = self.page;
        let pending = if feature == Feature::Home {
            self.pending_home_scans()
        } else {
            vec![feature]
        };
        if pending.is_empty() {
            return;
        }
        if pending.contains(&Feature::LargeFiles) {
            // A new attempt replaces the large-file authorization, even if it later fails.
            self.reports.remove(&Feature::LargeFiles);
            self.selected.remove(&Feature::LargeFiles);
        }
        let locations = vec![features::duplicates::Location::cleanable(
            &self.duplicate_drive,
        )];
        let minimum = Self::DUPLICATE_MINIMUMS[self.duplicate_minimum];
        if pending.contains(&Feature::Duplicates) {
            self.invalidate_duplicates();
        }
        let days = self.days;
        self.scan_elapsed.remove(&feature);
        if feature == Feature::Home {
            for f in &pending {
                self.scan_elapsed.remove(f);
            }
        }
        self.start(feature, "Scanning", move |store, ctx, tx| {
            if feature == Feature::Home {
                let errors = home::scan(&store, &pending, &locations, minimum, &ctx, |r| {
                    let _ = tx.send(Event::Report(r));
                });
                if !errors.is_empty() {
                    return Ok(JobValue::Operation(
                        Feature::Home,
                        OperationResult {
                            errors,
                            ..Default::default()
                        },
                    ));
                }
            } else {
                let report = if feature == Feature::Duplicates {
                    features::duplicates::scan_locations(&locations, minimum, &ctx)?
                } else {
                    home::scan_feature(feature, &store, None, days, &ctx)?
                };
                tx.send(Event::Report(report))?;
            }
            Ok(JobValue::Scan)
        });
    }
    pub fn choose_folder(&self, feature: Feature) {
        let tx = self.tx.clone();
        std::thread::spawn(move || {
            let folder = shell::choose_folder("CPlus Cleaner");
            let _ = tx.send(Event::Folder(feature, folder));
        });
    }
    fn open_dialog(&mut self, d: Dialog) -> Task<Message> {
        self.menu = false;
        if let Some((id, _)) = self.windows.iter().find(|(_, v)| **v == d) {
            return window::gain_focus(*id);
        }
        if d == Dialog::Settings && self.edit.language.is_empty() {
            self.edit.language = self.i18n.language.code().into();
        }
        if d == Dialog::Activity {
            self.activities = self.store.activities();
            self.activity_selected.clear();
            self.activity_action.clear();
            self.activity_result = None;
        }
        if d == Dialog::Updates && !self.busy() {
            self.release = None;
            self.update_error.clear();
            self.start(Feature::Home, "Check for Updates", |_, _, _| {
                Ok(JobValue::Release(cplus_core::updates::check()?))
            });
        }
        if let Dialog::History(f) = d {
            self.load_history(f);
        }
        let size = match d {
            Dialog::Activity => Size::new(850.0, 530.0),
            Dialog::History(_) | Dialog::Residue => Size::new(850.0, 620.0),
            Dialog::Settings => Size::new(507.0, 525.0),
            Dialog::Donate => Size::new(520.0, 640.0),
            Dialog::About => Size::new(340.0, 440.0),
            Dialog::Feedback => Size::new(740.0, 640.0),
            Dialog::Updates => Size::new(320.0, 304.0),
            Dialog::Confirm | Dialog::Information => {
                ui::dialogs::prompt_size(self, d == Dialog::Confirm)
            }
        };
        let (id, task) = window::open(window::Settings {
            size,
            resizable: !matches!(
                d,
                Dialog::Settings | Dialog::Updates | Dialog::Confirm | Dialog::Information
            ),
            decorations: true,
            minimizable: !matches!(d, Dialog::Updates | Dialog::Confirm | Dialog::Information),
            min_size: Some(
                if matches!(
                    d,
                    Dialog::About
                        | Dialog::Updates
                        | Dialog::Settings
                        | Dialog::Confirm
                        | Dialog::Information
                ) {
                    size
                } else if d == Dialog::Activity {
                    Size::new(800.0, 400.0)
                } else if d == Dialog::Feedback {
                    Size::new(720.0, 640.0)
                } else {
                    Size::new(450.0, 350.0)
                },
            ),
            position: window::Position::Centered,
            exit_on_close_request: false,
            icon: window_icon(),
            ..Default::default()
        });
        self.windows.insert(id, d);
        task.map(Message::Opened)
    }
    fn open_tray_menu(&mut self, x: i32, y: i32) -> Task<Message> {
        let close = self.tray_menu.take().map(window::close);
        let (id, task) = window::open(window::Settings {
            size: ui::tray::SIZE,
            visible: false,
            decorations: false,
            resizable: false,
            minimizable: false,
            level: window::Level::AlwaysOnTop,
            exit_on_close_request: false,
            platform_specific: window::settings::PlatformSpecific {
                skip_taskbar: true,
                undecorated_shadow: true,
                corner_preference: window::settings::platform::CornerPreference::Round,
                ..Default::default()
            },
            ..Default::default()
        });
        self.tray_menu = Some(id);
        Task::batch(close.into_iter().chain(std::iter::once(task.then(move |_| {
            window::raw_id::<Message>(id).map(move |hwnd| Message::TrayReady(id, hwnd, x, y))
        }))))
    }
    fn open_monitor(&mut self, tab: crate::monitor::Tab, x: i32, y: i32) -> Task<Message> {
        if let Some(id) = self.monitor.window {
            if self.monitor.tab == tab {
                self.monitor.window = None;
                return window::close(id);
            }
            self.monitor.tab = tab;
            return window::gain_focus(id);
        }
        let (x, y) = if x == 0 && y == 0 {
            cplus_platform_windows::monitor::taskbar::cursor_position()
        } else {
            (x, y)
        };
        self.monitor.tab = tab;
        let (id, open) = window::open(window::Settings {
            size: Size::new(390.0, 620.0),
            visible: false,
            decorations: false,
            resizable: false,
            minimizable: false,
            level: window::Level::AlwaysOnTop,
            exit_on_close_request: false,
            platform_specific: window::settings::PlatformSpecific {
                skip_taskbar: true,
                undecorated_shadow: true,
                corner_preference: window::settings::platform::CornerPreference::Round,
                ..Default::default()
            },
            ..Default::default()
        });
        self.monitor.window = Some(id);
        self.monitor.focused = false;
        open.then(move |_| {
            window::raw_id::<Message>(id).map(move |hwnd| Message::MonitorReady(id, hwnd, x, y))
        })
    }
    fn load_history(&mut self, f: Feature) {
        self.start(f, "Loading history", move |store, ctx, _| {
            let rows = match f {
                Feature::Compression => features::app_compression::history(&store, true, &ctx),
                Feature::AppMover => features::app_mover::history(&store),
                Feature::LargeFiles => features::large_files::history(&store),
                _ => popup_blocker::load_state(&store)
                    .history
                    .into_values()
                    .flatten()
                    .collect(),
            };
            Ok(JobValue::History(f, rows))
        });
    }
    fn apply_language(&mut self, language: Language) {
        self.language = language;
        self.i18n = I18n::new(language);
        self.ranges = [
            "All time",
            "Within 7 days",
            "Within 30 days",
            "Within 180 days",
        ]
        .map(|s| self.t(s))
        .to_vec();
        self.range = self.ranges[[0, 7, 30, 180]
            .iter()
            .position(|d| *d == self.days)
            .unwrap_or(0)]
        .clone();
    }
    fn preferences_to_save(&self) -> Settings {
        let mut settings = self.edit.clone();
        // A partially typed time must not prevent language and toggle changes.
        if settings.validate().is_err() {
            settings.cleanup_time = self.settings.cleanup_time.clone();
        }
        settings
    }
    fn save_preferences(&mut self) {
        self.settings_save_due = None;
        if self.settings_saving {
            return;
        }
        let settings = self.preferences_to_save();
        if settings == self.settings {
            return;
        }
        let previous = self.settings.clone();
        let store = self.store.clone();
        let tx = self.tx.clone();
        self.settings_saving = true;
        std::thread::spawn(move || {
            let result = (|| -> anyhow::Result<Settings> {
                settings.validate()?;
                if !store.isolated {
                    if settings.scheduled_cleanup != previous.scheduled_cleanup
                        || settings.cleanup_time != previous.cleanup_time
                    {
                        system_slim::configure_schedule(&settings)?;
                    }
                    if settings.autostart != previous.autostart {
                        system_slim::configure_startup(&settings)?;
                    }
                }
                store.save_settings(&settings)?;
                Ok(settings)
            })()
            .map_err(|e| e.to_string());
            let _ = tx.send(Event::SettingsSaved(result));
        });
    }
    fn receive(&mut self, event: Event) -> Task<Message> {
        match event {
            Event::Report(mut report) => {
                if report.feature == Feature::Duplicates {
                    features::duplicates::set_keeper_rule(&mut report, self.duplicate_keeper);
                    self.duplicate_page = 0;
                    self.duplicate_entry_pages.clear();
                }
                self.issues.extend(
                    report
                        .diagnostics
                        .iter()
                        .take(100usize.saturating_sub(self.issues.len()))
                        .cloned(),
                );
                let f = report.feature;
                if self.job.as_ref().is_some_and(|j| j.name == "Scanning") && !report.cancelled {
                    self.scan_elapsed.insert(f, report.elapsed_ms);
                }
                let chosen = if matches!(f, Feature::Drive | Feature::Chat | Feature::Privacy) {
                    report
                        .categories
                        .iter()
                        .filter(|c| c.default_selected && !c.view_only && c.count > 0)
                        .map(|c| c.id.clone())
                        .collect()
                } else {
                    BTreeSet::new()
                };
                self.selected.insert(f, chosen);
                if f == Feature::Popup {
                    let mut engine = self.engine.lock().unwrap_or_else(|e| e.into_inner());
                    engine.groups = report.popup.clone();
                    engine.state = popup_blocker::load_state(&self.store);
                }
                self.reports.insert(f, Arc::new(report));
            }
            Event::PopupRestored(groups) => {
                self.reports.insert(
                    Feature::Popup,
                    Arc::new(ScanResult {
                        feature: Feature::Popup,
                        popup: groups,
                        ..Default::default()
                    }),
                );
            }
            Event::Disks(disks) => self.disks = disks,
            Event::Residue(report) => {
                self.residue = Some(Arc::new(report));
                self.residue_selected.clear();
                return self.open_dialog(Dialog::Residue);
            }
            Event::Folder(feature, Some(folder)) => match feature {
                Feature::AppMover => {
                    self.destination_apps = folder.clone();
                    let _ = self.store.save_destination(true, &folder);
                }
                Feature::LargeFiles => {
                    self.destination_files = folder.clone();
                    let _ = self.store.save_destination(false, &folder);
                }
                _ => {}
            },
            Event::ChatFolder(app, Some(folder)) => {
                if let Err(e) =
                    features::chat_cleanup::save_folder(&self.store, &app, Some(&folder))
                {
                    self.info = e.to_string();
                    return self.open_dialog(Dialog::Information);
                }
            }
            Event::Folder(_, None) | Event::ChatFolder(_, None) => {}
            Event::SettingsSaved(result) => {
                self.settings_saving = false;
                match result {
                    Ok(saved) => {
                        self.settings = saved;
                        // Serialize writes so rapid edits cannot finish out of order.
                        self.save_preferences();
                    }
                    Err(error) => {
                        self.info = error;
                        self.settings_save_due = None;
                        self.quit = false;
                        return self.open_dialog(Dialog::Information);
                    }
                }
                if self.quit && !self.busy() && !self.settings_saving {
                    return iced::exit();
                }
            }
            Event::Finished(value) => {
                let timed = self.job.as_ref().is_none_or(|j| {
                    !matches!(j.name.as_str(), "Loading history" | "Check for Updates")
                });
                let elapsed = if timed {
                    self.job.as_ref().map(|j| j.ctx.elapsed_ms()).unwrap_or(0)
                } else {
                    0
                };
                if let Some(job) = self.job.as_ref().filter(|j| j.name == "Scanning")
                    && value.is_ok()
                    && !job.ctx.cancel.cancelled()
                {
                    self.scan_elapsed.insert(job.ctx.feature, elapsed);
                }
                let cancelled = self.job.as_ref().is_some_and(|j| j.ctx.cancel.cancelled());
                self.job = None;
                self.status = self.t(if cancelled { "Stopped" } else { "Completed" });
                self.status_elapsed = timed.then_some(elapsed);
                if self.quit && !self.settings_saving {
                    return iced::exit();
                }
                match value {
                    Err(error) => {
                        self.status = self.t(if cancelled {
                            "Stopped"
                        } else {
                            "Operation failed"
                        });
                        self.issues = vec![Issue {
                            path: String::new(),
                            message: error.clone(),
                        }];
                        self.update_error = error.clone();
                    }
                    Ok(JobValue::Scan) => {
                        self.status = self.t(if cancelled {
                            "Stopped"
                        } else {
                            "Scan complete"
                        });
                    }
                    Ok(JobValue::Operation(f, result)) => {
                        if let Some(selected) = self.selected.get_mut(&f) {
                            for id in &result.succeeded {
                                selected.remove(id);
                            }
                        }
                        if let Some(report) = self.residue.as_mut() {
                            let r = Arc::make_mut(report);
                            r.files.retain(|f| !result.succeeded.contains(&f.id));
                            r.registry_residues
                                .retain(|f| !result.succeeded.contains(&f.id));
                            features::drive_cleanup::fill_categories(&mut r.categories, &r.files);
                            for c in &mut r.categories {
                                if r.registry_residues.iter().any(|key| key.id == c.id) {
                                    c.count = 1;
                                }
                            }
                            r.categories.retain(|c| c.count > 0);
                        }
                        self.status = format!(
                            "{} {} · {}",
                            self.t(if result.ok() {
                                "Completed"
                            } else {
                                "Partial completion"
                            }),
                            result.count,
                            bytes(result.bytes)
                        );
                        self.issues = result.errors.clone();
                        if let Some(report) = self.reports.get_mut(&f) {
                            let r = Arc::make_mut(report);
                            r.files.retain(|x| !result.succeeded.contains(&x.id));
                            r.apps.retain(|x| !result.succeeded.contains(&x.id));
                            r.privacy.retain(|x| !result.succeeded.contains(&x.id));
                            if f == Feature::Duplicates {
                                features::duplicates::refresh_categories(r);
                            } else {
                                features::drive_cleanup::fill_categories(
                                    &mut r.categories,
                                    &r.files,
                                );
                            }
                            if f == Feature::Privacy {
                                for c in &mut r.categories {
                                    c.count =
                                        r.privacy.iter().filter(|r| r.category == c.id).count()
                                            as u64;
                                }
                            }
                            for (id, size) in &result.system_sizes {
                                if let Some(category) =
                                    r.categories.iter_mut().find(|c| c.id == *id)
                                {
                                    category.bytes = *size;
                                    category.count = u64::from(*size > 0);
                                }
                            }
                        }
                        if self.windows.values().any(|d| *d == Dialog::History(f)) {
                            self.load_history(f);
                        }
                        let tx = self.tx.clone();
                        std::thread::spawn(move || {
                            let _ = tx.send(Event::Disks(filesystem::disks()));
                        });
                    }
                    Ok(JobValue::History(f, rows)) => {
                        self.history.insert(f, rows);
                        self.status.clear();
                    }
                    Ok(JobValue::Release(release)) => self.release = Some(release),
                    Ok(JobValue::System(output)) => {
                        self.component_analysis = output;
                        self.status = self.t("Completed");
                        let tx = self.tx.clone();
                        std::thread::spawn(move || {
                            if let Ok(r) = system_slim::scan(&Context::new(Feature::System)) {
                                let _ = tx.send(Event::Report(r));
                            }
                            let _ = tx.send(Event::Disks(filesystem::disks()));
                        });
                    }
                }
            }
        }
        Task::none()
    }
    pub fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::WindowFocused(id) => {
                if self.monitor.window == Some(id) {
                    self.monitor.focused = true;
                }
            }
            Message::MonitorTab(tab) => self.monitor.tab = tab,
            Message::MonitorReady(id, hwnd, x, y) => {
                if self.monitor.window == Some(id) {
                    cplus_platform_windows::windows::show_tray_menu(hwnd, x, y);
                }
            }
            Message::MonitorClose => {
                if let Some(id) = self.monitor.window.take() {
                    return window::close(id);
                }
            }
            Message::MonitorMain | Message::MonitorCleanup => {
                if matches!(message, Message::MonitorCleanup) {
                    self.page = Feature::Drive;
                }
                let close = self.monitor.window.take().map(window::close);
                return Task::batch(close.into_iter().chain([
                    window::set_mode(self.main, window::Mode::Windowed),
                    window::gain_focus(self.main),
                ]));
            }
            Message::Tick => {
                if cplus_platform_windows::processes::activation_requested(if self.config.preview {
                    "CPlusCleaner-Rust-Preview"
                } else {
                    "CPlusCleaner-Rust"
                }) {
                    return Task::batch([
                        window::set_mode(self.main, window::Mode::Windowed),
                        window::gain_focus(self.main),
                    ]);
                }
                let events = self.rx.try_iter().collect::<Vec<_>>();
                let mut tasks = vec![];
                self.monitor.poll();
                let actions: Vec<_> = self
                    .monitor
                    .panel
                    .as_ref()
                    .map(|p| p.events.try_iter().collect())
                    .unwrap_or_default();
                for action in actions {
                    use cplus_platform_windows::monitor::taskbar::Action;
                    tasks.push(match action {
                        Action::Open(tab, x, y) => {
                            self.open_monitor(crate::monitor::Tab::ALL[tab], x, y)
                        }
                        Action::Menu(x, y) => self.open_tray_menu(x, y),
                    });
                }
                for event in events {
                    tasks.push(self.receive(event));
                }
                if self
                    .settings_save_due
                    .is_some_and(|due| Instant::now() >= due)
                {
                    self.save_preferences();
                }
                if let Some(job) = &self.job {
                    self.progress = job.ctx.snapshot();
                }
                if self
                    .config
                    .quit_after
                    .is_some_and(|n| self.started.elapsed().as_secs() >= n)
                {
                    return iced::exit();
                }
                if self.tray.is_some() {
                    while let Ok(event) = tray_icon::TrayIconEvent::receiver().try_recv() {
                        match event {
                            tray_icon::TrayIconEvent::Click {
                                button: tray_icon::MouseButton::Right,
                                button_state: tray_icon::MouseButtonState::Up,
                                position,
                                ..
                            } => tasks
                                .push(self.open_tray_menu(position.x as i32, position.y as i32)),
                            tray_icon::TrayIconEvent::Click {
                                button: tray_icon::MouseButton::Left,
                                button_state: tray_icon::MouseButtonState::Up,
                                ..
                            } => {
                                if let Some(id) = self.tray_menu.take() {
                                    tasks.push(window::close(id));
                                }
                                tasks.push(Task::batch([
                                    window::set_mode(self.main, window::Mode::Windowed),
                                    window::gain_focus(self.main),
                                ]));
                            }
                            _ => {}
                        }
                    }
                }
                return Task::batch(tasks);
            }
            Message::TrayReady(id, hwnd, x, y) => {
                if self.tray_menu == Some(id) {
                    cplus_platform_windows::windows::show_tray_menu(hwnd, x, y);
                }
            }
            Message::TrayDismiss(id) => {
                if self.monitor.window == Some(id) && self.monitor.focused {
                    self.monitor.window = None;
                    return window::close(id);
                }
                if self.tray_menu == Some(id) {
                    self.tray_menu = None;
                    return window::close(id);
                }
            }
            Message::TrayAction(action) => {
                let close = self.tray_menu.take().map(window::close);
                let navigate = matches!(*action, Message::Navigate(_));
                let action = self.update(*action);
                let mut tasks: Vec<_> = close.into_iter().chain([action]).collect();
                if navigate {
                    self.scan();
                    tasks.push(window::set_mode(self.main, window::Mode::Windowed));
                    tasks.push(window::gain_focus(self.main));
                }
                return Task::batch(tasks);
            }
            Message::Opened(id) => {
                if id == self.main
                    && !self.config.background
                    && !(self.config.preview && self.config.page.as_deref() == Some("monitor"))
                {
                    return window::gain_focus(id);
                }
            }
            Message::Close(id) => {
                if self.monitor.window == Some(id) {
                    self.monitor.window = None;
                    return window::close(id);
                }
                if self.tray_menu == Some(id) {
                    self.tray_menu = None;
                    return window::close(id);
                }
                if id == self.main {
                    if self.settings.close_to_tray && self.tray.is_some() {
                        return window::set_mode(id, window::Mode::Hidden);
                    }
                    return self.update(Message::Exit);
                }
                self.windows.remove(&id);
                return window::close(id);
            }
            Message::Dismiss(id) => {
                self.windows.remove(&id);
                return window::close(id);
            }
            Message::Exit => {
                self.monitor.enable(false);
                self.save_preferences();
                if let Some(job) = &self.job {
                    job.ctx.cancel.cancel();
                    self.quit = true;
                    return window::set_mode(self.main, window::Mode::Hidden);
                }
                if self.settings_saving {
                    self.quit = true;
                    return window::set_mode(self.main, window::Mode::Hidden);
                }
                self.stop.store(true, Ordering::Relaxed);
                return iced::exit();
            }
            Message::NavHover(feature, inside) => {
                if inside {
                    self.hovered_nav = Some(feature);
                } else if self.hovered_nav == Some(feature) {
                    self.hovered_nav = None;
                }
            }
            Message::Navigate(f) => {
                self.page = f;
                self.search.clear();
                self.menu = false;
                if f == Feature::System && !self.reports.contains_key(&f) && !self.busy() {
                    self.scan();
                }
            }
            Message::Menu => self.menu = !self.menu,
            Message::Dialog(d) => return self.open_dialog(d),
            Message::Scan => self.scan(),
            Message::CancelLargeScan => {
                if let Some(job) = &self.job
                    && job.name == "Scanning"
                    && matches!(
                        job.ctx.feature,
                        Feature::LargeFiles | Feature::Home | Feature::Duplicates
                    )
                {
                    job.ctx.cancel.cancel();
                }
            }
            Message::Search(s) => {
                self.search = s;
                self.duplicate_page = 0;
                self.duplicate_entry_pages.clear();
            }
            Message::DuplicateDrive(root) => {
                if self.busy() {
                    return Task::none();
                }
                if self.disks.iter().any(|d| d.root == root) {
                    self.duplicate_drive = root;
                }
            }
            Message::DuplicateMinimum(value) => {
                if !self.busy()
                    && let Some(index) = self
                        .duplicate_minimum_options()
                        .iter()
                        .position(|s| *s == value)
                {
                    self.duplicate_minimum = index;
                }
            }
            Message::DuplicateSmartSelect => {
                if !self.busy() {
                    let ids = self
                        .reports
                        .get(&Feature::Duplicates)
                        .map(|r| features::duplicates::suggested_ids(r, None))
                        .unwrap_or_default();
                    self.selected.insert(Feature::Duplicates, ids);
                }
            }
            Message::DuplicateSelect(ids) => {
                if self.busy() || self.page != Feature::Duplicates {
                    return Task::none();
                }
                let eligible = self
                    .report()
                    .map(|r| features::duplicates::suggested_ids(r, None))
                    .unwrap_or_default();
                let ids: Vec<_> = ids.into_iter().filter(|id| eligible.contains(id)).collect();
                let selected = self.selected.entry(Feature::Duplicates).or_default();
                let all = ids.iter().all(|id| selected.contains(id));
                for id in ids {
                    if all {
                        selected.remove(&id);
                    } else {
                        selected.insert(id);
                    }
                }
            }
            Message::DuplicateDelete(ids) => {
                if self.busy() || self.page != Feature::Duplicates {
                    return Task::none();
                }
                let eligible = self
                    .report()
                    .map(|r| features::duplicates::suggested_ids(r, None))
                    .unwrap_or_default();
                let selected = self.choices(Feature::Duplicates);
                let ids: Vec<_> = ids
                    .into_iter()
                    .filter(|id| eligible.contains(id) && selected.contains(id))
                    .collect();
                if ids.is_empty() {
                    return Task::none();
                }
                self.pending = Some(Pending {
                    feature: Feature::Duplicates,
                    action: Action::Delete,
                    ids,
                });
                return self.open_dialog(Dialog::Confirm);
            }
            Message::DuplicateEntryPage(group, page) => {
                self.duplicate_entry_pages.insert(group, page);
            }
            Message::Range(s) => {
                self.days = [0, 7, 30, 180][self.ranges.iter().position(|v| v == &s).unwrap_or(0)];
                self.range = s;
            }
            Message::Sort(key) => {
                if self.sort == key {
                    self.descending = !self.descending;
                } else {
                    self.sort = key;
                    self.descending = true;
                }
            }
            Message::Toggle(id) => {
                if self.page == Feature::Duplicates
                    && (self.busy()
                        || self.report().is_none_or(|r| {
                            !features::duplicates::suggested_ids(r, None).contains(&id)
                        }))
                {
                    return Task::none();
                }
                if self.page == Feature::Duplicates
                    && self
                        .report()
                        .is_some_and(|r| features::duplicates::is_retained(r, &id))
                {
                    return Task::none();
                }
                let selected = if self.windows.values().any(|d| *d == Dialog::Residue) {
                    &mut self.residue_selected
                } else {
                    self.selected.entry(self.page).or_default()
                };
                if !selected.remove(&id) {
                    selected.insert(id);
                }
            }
            Message::Expand(id) => {
                if !self.expanded.remove(&id) {
                    self.expanded.insert(id);
                }
            }
            Message::DuplicateExpandAll => {
                if self.page == Feature::Duplicates
                    && !self.busy()
                    && let Some(report) = self.report()
                {
                    let ids = ui::pages::duplicate_expansion_ids(report);
                    if ids.is_subset(&self.expanded) {
                        self.expanded.retain(|id| !ids.contains(id));
                    } else {
                        self.expanded.extend(ids);
                    }
                }
            }
            Message::Group(id) => {
                if self.page == Feature::Duplicates {
                    if self.busy() {
                        return Task::none();
                    }
                    let ids = self
                        .report()
                        .map(|r| features::duplicates::suggested_ids(r, Some(&id)))
                        .unwrap_or_default();
                    let selected = self.selected.entry(self.page).or_default();
                    let all = ids.iter().all(|id| selected.contains(id));
                    for id in ids {
                        if all {
                            selected.remove(&id);
                        } else {
                            selected.insert(id);
                        }
                    }
                    return Task::none();
                }
                let Some(r) = self.report() else {
                    return Task::none();
                };
                let Some(_) = r.categories.iter().find(|c| c.id == id) else {
                    return Task::none();
                };
                let files = r
                    .files
                    .iter()
                    .filter(|f| f.category == id && f.blocked.is_empty())
                    .map(|f| f.id.clone())
                    .collect::<Vec<_>>();
                let selected = self.selected.entry(self.page).or_default();
                let keep = usize::from(self.page == Feature::Duplicates);
                let all = files.iter().skip(keep).all(|id| selected.contains(id));
                for (id_index, id) in files.iter().enumerate() {
                    if all {
                        selected.remove(id);
                    } else if self.page != Feature::Duplicates || id_index > 0 {
                        selected.insert(id.clone());
                    }
                }
            }
            Message::CategoryGroup(group) => {
                if self.busy() {
                    return Task::none();
                }
                let ids = self
                    .report()
                    .map(|r| {
                        r.categories
                            .iter()
                            .filter(|c| c.group == group && category_selectable(r, c))
                            .map(|c| c.id.clone())
                            .collect::<Vec<_>>()
                    })
                    .unwrap_or_default();
                let selected = self.selected.entry(self.page).or_default();
                let all = ids.iter().all(|id| selected.contains(id));
                for id in ids {
                    if all {
                        selected.remove(&id);
                    } else {
                        selected.insert(id);
                    }
                }
            }
            Message::All(on) => {
                if self.busy() {
                    return Task::none();
                }
                if self.page == Feature::Duplicates {
                    if self.busy() {
                        return Task::none();
                    }
                    let ids = if on {
                        self.report()
                            .map(|r| features::duplicates::suggested_ids(r, None))
                            .unwrap_or_default()
                    } else {
                        BTreeSet::new()
                    };
                    self.selected.insert(self.page, ids);
                    return Task::none();
                }
                let mut selected = BTreeSet::new();
                if on && let Some(r) = self.report() {
                    if !r.apps.is_empty() {
                        selected.extend(
                            r.apps
                                .iter()
                                .filter(|a| a.blocked.is_empty())
                                .map(|a| a.id.clone()),
                        );
                    } else if matches!(self.page, Feature::LargeFiles | Feature::Duplicates) {
                        for c in &r.categories {
                            selected.extend(
                                r.files
                                    .iter()
                                    .filter(|f| f.category == c.id && f.blocked.is_empty())
                                    .skip(if self.page == Feature::Duplicates {
                                        1
                                    } else {
                                        0
                                    })
                                    .map(|f| f.id.clone()),
                            );
                        }
                    } else {
                        selected.extend(
                            r.categories
                                .iter()
                                .filter(|c| category_selectable(r, c))
                                .map(|c| c.id.clone()),
                        );
                    }
                }
                self.selected.insert(self.page, selected);
            }
            Message::Defaults => {
                if let Some(report) = self.report() {
                    let selected = report
                        .categories
                        .iter()
                        .filter(|c| {
                            c.default_selected
                                && !c.view_only
                                && (c.count > 0 || report.token.is_empty())
                        })
                        .map(|c| c.id.clone())
                        .collect();
                    self.selected.insert(self.page, selected);
                }
            }
            Message::Request(action, id) => {
                if self.busy() {
                    return Task::none();
                }
                if let Action::System(a) = action
                    && matches!(
                        a,
                        system_slim::Action::PagefileSettings
                            | system_slim::Action::AnalyzeComponents
                    )
                {
                    self.start(Feature::System, "Working", move |store, ctx, _| {
                        Ok(JobValue::System(system_slim::perform(&store, a, &ctx)?))
                    });
                    return Task::none();
                }
                if matches!(action, Action::System(_)) {
                    self.pending = Some(Pending {
                        feature: self.page,
                        action,
                        ids: vec![],
                    });
                    return self.open_dialog(Dialog::Confirm);
                }
                if self.page == Feature::Home {
                    return self.update(Message::Navigate(Feature::Drive));
                }
                if self.page == Feature::Popup {
                    return self.update(Message::PopupToggle("*".into(), true));
                }
                let chosen = self.choices(self.page);
                let ids = id
                    .map(|id| {
                        if matches!(self.page, Feature::LargeFiles | Feature::Duplicates)
                            && let Some(r) = self.report()
                            && r.categories.iter().any(|c| c.id == id)
                        {
                            return r
                                .files
                                .iter()
                                .filter(|f| f.category == id && f.blocked.is_empty())
                                .filter(|f| {
                                    self.page != Feature::Duplicates
                                        || !features::duplicates::is_retained(r, &f.id)
                                })
                                .filter(|f| {
                                    !matches!(self.page, Feature::LargeFiles | Feature::Duplicates)
                                        || chosen.contains(&f.id)
                                })
                                .map(|f| f.id.clone())
                                .collect();
                        }
                        vec![id]
                    })
                    .unwrap_or_else(|| chosen.into_iter().collect());
                if ids.is_empty() && !matches!(action, Action::System(_)) {
                    return Task::none();
                }
                self.pending = Some(Pending {
                    feature: self.page,
                    action,
                    ids,
                });
                return self.open_dialog(Dialog::Confirm);
            }
            Message::Confirm => {
                let Some(pending) = self.pending.take() else {
                    return Task::none();
                };
                let feature = pending.feature;
                let report = self.reports.get(&feature).cloned();
                let dest = if feature == Feature::AppMover {
                    self.destination_apps.clone()
                } else {
                    self.destination_files.clone()
                };
                let action = pending.action;
                let ids = pending.ids;
                self.start(feature, "Working", move |store, ctx, tx| {
                    if let Action::System(action) = action {
                        return Ok(JobValue::System(system_slim::perform(
                            &store, action, &ctx,
                        )?));
                    }
                    if let Action::Restore(id) = action {
                        let result = if feature == Feature::Compression {
                            features::app_compression::restore(&store, &id, &ctx)?
                        } else {
                            features::app_mover::restore(&store, &id, &ctx)?
                        };
                        if result.ok()
                            && let Ok(report) = home::scan_feature(feature, &store, None, 0, &ctx)
                        {
                            let _ = tx.send(Event::Report(report));
                        }
                        return Ok(JobValue::Operation(feature, result));
                    }
                    let report = report.ok_or_else(|| anyhow::anyhow!("Scan first"))?;
                    let apps = report
                        .apps
                        .iter()
                        .filter(|a| ids.contains(&a.id))
                        .cloned()
                        .collect::<Vec<_>>();
                    let result = match action {
                        Action::Delete => home::delete(&store, &report, &ids, &ctx),
                        Action::Move if feature == Feature::LargeFiles => {
                            features::large_files::move_files(
                                &store,
                                &report,
                                &ids.iter().cloned().collect(),
                                &dest,
                                &ctx,
                            )
                        }
                        Action::Move => features::app_mover::move_apps(&store, &apps, &dest, &ctx),
                        Action::Compress => {
                            features::app_compression::compress_apps(&store, &apps, &ctx)
                        }
                        Action::Uninstall => features::uninstall::uninstall(&store, &apps, &ctx),
                        _ => unreachable!(),
                    };
                    if matches!(action, Action::Uninstall) {
                        let removed = apps
                            .iter()
                            .filter(|a| result.succeeded.contains(&a.id))
                            .cloned()
                            .collect::<Vec<_>>();
                        if !removed.is_empty() {
                            let residues = features::uninstall::residue::scan(&removed, &ctx)?;
                            if !residues.files.is_empty() || !residues.registry_residues.is_empty()
                            {
                                let _ = tx.send(Event::Residue(residues));
                            }
                        }
                    }
                    Ok(JobValue::Operation(feature, result))
                });
                let ids = self
                    .windows
                    .iter()
                    .filter(|(_, d)| **d == Dialog::Confirm)
                    .map(|(id, _)| *id)
                    .collect::<Vec<_>>();
                for id in &ids {
                    self.windows.remove(id);
                }
                return Task::batch(ids.into_iter().map(window::close));
            }
            Message::Language(language) => {
                self.edit.language = language.code().into();
                self.apply_language(language);
                self.save_preferences();
            }
            Message::Setting(key, on) => {
                match key {
                    "autostart" => self.edit.autostart = on,
                    "schedule" => self.edit.scheduled_cleanup = on,
                    "tray" => self.edit.close_to_tray = on,
                    "monitor" => {
                        self.edit.taskbar_monitor = on;
                        self.monitor.enable(on);
                    }
                    _ => {}
                }
                self.save_preferences();
                if key == "monitor"
                    && !on
                    && let Some(id) = self.monitor.window.take()
                {
                    return window::close(id);
                }
            }
            Message::Time(value) => {
                self.edit.cleanup_time = value;
                self.settings_save_due = Some(Instant::now() + Duration::from_millis(600));
            }
            Message::SaveSettings => self.save_preferences(),
            Message::Folder(f) => self.choose_folder(f),
            Message::FolderForChat(id) => {
                let tx = self.tx.clone();
                std::thread::spawn(move || {
                    let _ = tx.send(Event::ChatFolder(id, shell::choose_folder("CPlus Cleaner")));
                });
            }
            Message::ResetChat(id) => {
                if let Err(error) = features::chat_cleanup::save_folder(&self.store, &id, None) {
                    self.info = error.to_string();
                    return self.open_dialog(Dialog::Information);
                }
            }
            Message::Open(url) => {
                if let Err(e) = shell::open(&url) {
                    self.info = e.to_string();
                    return self.open_dialog(Dialog::Information);
                }
            }
            Message::Reveal(path) => {
                let _ = shell::select_file(&path);
            }
            Message::HistoryRestore(f, id) => {
                self.pending = Some(Pending {
                    feature: f,
                    action: Action::Restore(id),
                    ids: vec![],
                });
                return self.open_dialog(Dialog::Confirm);
            }
            Message::PopupToggle(id, on) => {
                let mut e = self.engine.lock().unwrap_or_else(|e| e.into_inner());
                let result = if id == "*" {
                    let ids = e.groups.iter().map(|g| g.key.clone()).collect::<Vec<_>>();
                    ids.iter().try_for_each(|id| e.toggle(&self.store, id, on))
                } else {
                    e.toggle(&self.store, &id, on)
                };
                if let Err(error) = result {
                    self.status = error.to_string();
                    self.status_feature = self.page;
                    self.status_elapsed = None;
                }
            }
            Message::SelectActivity(id) => {
                if !self.activity_selected.remove(&id) {
                    self.activity_selected.insert(id);
                }
            }
            Message::SelectActivities(ids) => {
                if ids.iter().all(|id| self.activity_selected.contains(id)) {
                    self.activity_selected.clear();
                } else {
                    self.activity_selected = ids.into_iter().collect();
                }
            }
            Message::ActivityAction(action) => {
                self.activity_action = action;
                self.activity_selected.clear();
            }
            Message::ActivityResult(result) => {
                self.activity_result = result;
                self.activity_selected.clear();
            }
            Message::DeleteActivity => {
                let remaining = self
                    .activities
                    .iter()
                    .filter(|r| !self.activity_selected.contains(&r.id))
                    .cloned()
                    .collect::<Vec<_>>();
                if let Err(error) = self.store.replace_activities(&remaining) {
                    self.info = error.to_string();
                    return self.open_dialog(Dialog::Information);
                }
                self.activities = remaining;
                self.activity_selected.clear();
            }
            Message::ClearActivity => {
                if let Err(error) = self.store.replace_activities(&[]) {
                    self.info = error.to_string();
                    return self.open_dialog(Dialog::Information);
                }
                self.activities.clear();
                self.activity_selected.clear();
            }
            Message::CopyEnvironment => {
                return iced::clipboard::write(ui::dialogs::environment(self));
            }
            Message::Copy(text) => return iced::clipboard::write(text),
            Message::DeleteResidues => {
                if let Some(report) = self.residue.clone() {
                    let ids = self.residue_selected.iter().cloned().collect::<Vec<_>>();
                    self.start(Feature::Uninstall, "Working", move |_, ctx, _| {
                        Ok(JobValue::Operation(
                            Feature::Uninstall,
                            features::uninstall::residue::delete(&report, &ids, &ctx),
                        ))
                    })
                }
            }
        }
        Task::none()
    }
    pub fn view(&self, id: window::Id) -> Element<'_, Message> {
        if self.monitor.window == Some(id) {
            return ui::monitor::view(self);
        }
        if self.tray_menu == Some(id) {
            return ui::tray::view(self);
        }
        if id != self.main {
            if let Some(d) = self.windows.get(&id) {
                return ui::dialogs::view(self, id, d);
            }
            return empty("");
        }
        let english = self.i18n.language == Language::English;
        let mut nav = column![
            row![
                logo(49),
                column![
                    bold("CPlus Cleaner", 17),
                    small(format!(
                        "{}{}",
                        if self.i18n.language == Language::English {
                            String::new()
                        } else {
                            self.drive_letter()
                        },
                        self.t("Drive Cleanup Assistant")
                    ))
                ]
                .spacing(5)
            ]
            .spacing(12)
            .align_y(iced::Alignment::Center)
        ]
        .spacing(10)
        .padding([25, if english { 8 } else { 15 }]);
        for f in Feature::ALL {
            let active = self.page == f;
            let hovered = self.hovered_nav == Some(f);
            let has_metric = self
                .reports
                .get(&f)
                .is_some_and(|r| !r.token.is_empty() || f == Feature::Popup)
                && !matches!(f, Feature::Home | Feature::System);
            let mut item = row![
                nav_icon(f, active, 22),
                label(self.name(f))
                    .color(if hovered {
                        iced::Color::from_rgb8(0x08, 0x6b, 0x46)
                    } else if active {
                        theme::MINT
                    } else {
                        theme::INK
                    })
                    .size(if english { 12 } else { 14 })
                    .wrapping(if english {
                        iced::widget::text::Wrapping::None
                    } else {
                        iced::widget::text::Wrapping::Word
                    })
                    .width(Length::Fill)
            ]
            .spacing(if english {
                4
            } else if has_metric {
                11
            } else {
                13
            })
            .align_y(iced::Alignment::Center);
            if has_metric {
                item = item.push(
                    container(
                        bold(self.metric(f), 10)
                            .color(iced::Color::WHITE)
                            .wrapping(iced::widget::text::Wrapping::None),
                    )
                    .padding([0, 2])
                    .center_y(16)
                    .style(|_| container::Style {
                        background: Some(theme::CORAL.into()),
                        border: iced::Border {
                            radius: 2.0.into(),
                            ..Default::default()
                        },
                        ..Default::default()
                    }),
                );
            }
            nav = nav.push(
                iced::widget::mouse_area(
                    button(container(item).width(Length::Fill))
                        .padding([12, if english { 4 } else { 10 }])
                        .width(Length::Fill)
                        .on_press(Message::Navigate(f))
                        .style(move |_, status| iced::widget::button::Style {
                            background: if active
                                || hovered
                                || matches!(status, iced::widget::button::Status::Hovered)
                            {
                                Some(theme::PALE.into())
                            } else {
                                None
                            },
                            text_color: if hovered
                                || matches!(status, iced::widget::button::Status::Hovered)
                            {
                                iced::Color::from_rgb8(0x08, 0x6b, 0x46)
                            } else if active {
                                theme::MINT
                            } else {
                                theme::INK
                            },
                            border: iced::Border {
                                radius: 9.0.into(),
                                ..Default::default()
                            },
                            ..Default::default()
                        }),
                )
                .on_enter(Message::NavHover(f, true))
                .on_exit(Message::NavHover(f, false)),
            );
        }
        let sidebar = container(column![nav, Space::new().height(Length::Fill)])
            .width(238)
            .height(Length::Fill)
            .style(|_| container::Style {
                background: Some(iced::Color::WHITE.into()),
                border: iced::Border {
                    color: theme::LINE,
                    width: 1.0,
                    ..Default::default()
                },
                ..Default::default()
            });
        let mut activity_label = row![].spacing(6).align_y(iced::Alignment::Center);
        activity_label = activity_label.push(
                iced::widget::svg(iced::widget::svg::Handle::from_memory(
                    br#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8M3 3v5h5M12 7v5l4 2"/></svg>"#
                ))
                .width(16).height(16)
                .style(|_, _| iced::widget::svg::Style { color: Some(theme::MUTED) })
        );
        activity_label = activity_label.push(label(self.t("Activity Log")).color(theme::MUTED));
        let header = row![
            column![
                bold(self.name(self.page), 30),
                small(self.t(ui::pages::subtitle(self.page))).size(14)
            ]
            .spacing(5),
            Space::new().width(Length::Fill),
            iced::widget::button(activity_label)
                .padding([5, 8])
                .on_press(Message::Dialog(Dialog::Activity))
                .style(theme::link),
            link("☰", Message::Menu)
        ]
        .align_y(iced::Alignment::Start)
        .spacing(14);
        let body = column![header, ui::pages::view(self)].spacing(20);
        let main = container(body)
            .padding([28, 25])
            .width(Length::Fill)
            .height(Length::Fill)
            .style(theme::surface);
        if self.menu {
            let mut menu = iced::widget::Column::new().spacing(3);
            for (d, key) in [
                (Dialog::Settings, "Settings"),
                (Dialog::Updates, "Check for Updates"),
                (Dialog::Feedback, "Feedback"),
                (
                    Dialog::Donate,
                    if self.i18n.language == Language::English {
                        "Buy me a coffee"
                    } else {
                        "Donate"
                    },
                ),
                (Dialog::About, "About"),
            ] {
                menu = menu.push(menu_item(self.t(key), Message::Dialog(d)));
            }
            let overlay = container(row![
                Space::new().width(Length::Fill),
                container(menu).width(210).padding(8).style(theme::card)
            ])
            .padding(iced::Padding {
                top: 70.0,
                right: 25.0,
                bottom: 0.0,
                left: 0.0,
            });
            let dismiss =
                iced::widget::mouse_area(Space::new().width(Length::Fill).height(Length::Fill))
                    .on_press(Message::Menu);
            return row![sidebar, iced::widget::stack![main, dismiss, overlay]].into();
        }
        row![sidebar, main].into()
    }
}
pub fn window_icon() -> Option<window::Icon> {
    let image = image::load_from_memory(cplus_core::resource("assets/app-icon.png")?)
        .ok()?
        .into_rgba8();
    let (w, h) = image.dimensions();
    window::icon::from_rgba(image.into_raw(), w, h).ok()
}

pub fn category_selectable(report: &ScanResult, category: &Category) -> bool {
    !category.view_only && (category.count > 0 || report.token.is_empty())
}

#[cfg(test)]
mod selection_tests {
    use super::*;
    #[test]
    fn navigation_hover_updates_on_entry_and_ignores_an_older_menu_exit() {
        let data = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!(
            "../../validation-output/navigation-hover-test-{}",
            std::process::id()
        ));
        let (mut app, _) = App::new(Config {
            preview: true,
            data: Some(data),
            ..Default::default()
        });
        let _ = app.update(Message::NavHover(Feature::Drive, true));
        assert_eq!(app.hovered_nav, Some(Feature::Drive));
        let _ = app.update(Message::NavHover(Feature::Chat, true));
        let _ = app.update(Message::NavHover(Feature::Drive, false));
        assert_eq!(app.hovered_nav, Some(Feature::Chat));
        let _ = app.update(Message::NavHover(Feature::Chat, false));
        assert_eq!(app.hovered_nav, None);
        assert_eq!(app.page, Feature::Home);
        assert!(!app.busy());
    }
    #[test]
    fn update_checks_do_not_publish_elapsed_time_but_scans_still_do() {
        let data = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!(
            "../../validation-output/update-timing-test-{}",
            std::process::id()
        ));
        let (mut app, _) = App::new(Config {
            preview: true,
            data: Some(data),
            ..Default::default()
        });
        for (name, timed) in [
            ("Check for Updates", false),
            ("Loading history", false),
            ("Scanning", true),
        ] {
            app.job = Some(Job {
                ctx: Context::new(Feature::Home),
                name: name.into(),
            });
            let _ = app.receive(Event::Finished(Ok(JobValue::Scan)));
            assert_eq!(app.status_elapsed.is_some(), timed, "{name}");
        }
    }
    #[test]
    fn home_scan_preserves_completed_empty_results_and_retries_cancelled_results() {
        let data = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!(
            "../../validation-output/home-pending-test-{}",
            std::process::id()
        ));
        let (mut app, _) = App::new(Config {
            preview: true,
            data: Some(data),
            ..Default::default()
        });
        assert_eq!(app.pending_home_scans(), Feature::HOME);
        for f in Feature::HOME {
            app.reports.insert(
                f,
                Arc::new(ScanResult {
                    feature: f,
                    ..Default::default()
                }),
            );
            app.scan_elapsed.insert(f, 42);
        }
        app.page = Feature::Home;
        app.scan();
        assert!(!app.busy());
        assert_eq!(app.scan_elapsed.get(&Feature::Duplicates), Some(&42));
        assert!(app.reports.contains_key(&Feature::LargeFiles));
        Arc::make_mut(app.reports.get_mut(&Feature::Duplicates).unwrap()).cancelled = true;
        assert_eq!(app.pending_home_scans(), [Feature::Duplicates]);
        app.reports.remove(&Feature::Chat);
        assert_eq!(
            app.pending_home_scans(),
            [Feature::Chat, Feature::Duplicates]
        );
    }

    #[test]
    fn monitor_details_reuse_one_window_without_starting_cleanup() {
        let data = std::env::temp_dir().join(format!("cplus-monitor-test-{}", common::token()));
        let (mut app, _) = App::new(Config {
            preview: true,
            data: Some(data),
            ..Default::default()
        });
        assert!(!app.edit.taskbar_monitor);
        let _ = app.open_monitor(crate::monitor::Tab::Cpu, 500, 500);
        let id = app.monitor.window.unwrap();
        let _ = app.open_monitor(crate::monitor::Tab::Memory, 500, 500);
        assert_eq!(app.monitor.window, Some(id));
        assert_eq!(app.monitor.tab, crate::monitor::Tab::Memory);
        assert!(!app.busy());
        let _ = app.update(Message::TrayDismiss(id));
        assert_eq!(app.monitor.window, Some(id));
        let _ = app.update(Message::WindowFocused(id));
        let _ = app.update(Message::TrayDismiss(id));
        assert!(app.monitor.window.is_none());
    }

    #[test]
    fn tray_navigation_starts_scan_for_its_page_without_replacing_an_active_job() {
        let data = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!(
            "../../validation-output/tray-autoscan-test-{}",
            std::process::id()
        ));
        let fixture = std::env::temp_dir().join(format!("cplus-tray-scan-{}", common::token()));
        std::fs::create_dir_all(&fixture).unwrap();
        // Windows runners may expose TEMP through an 8.3 alias.
        let fixture = std::fs::canonicalize(fixture).unwrap();
        std::fs::write(fixture.join("a.txt"), b"owned duplicate").unwrap();
        std::fs::write(fixture.join("b.txt"), b"owned duplicate").unwrap();
        let (mut app, _) = App::new(Config {
            preview: true,
            data: Some(data.join("profile")),
            ..Default::default()
        });
        let _ = app.update(Message::TrayAction(Box::new(Message::Dialog(
            Dialog::Settings,
        ))));
        assert!(!app.busy());
        app.duplicate_drive = fixture.display().to_string();
        app.duplicate_minimum = 0;
        let _ = app.update(Message::TrayAction(Box::new(Message::Navigate(
            Feature::Duplicates,
        ))));
        assert_eq!(app.page, Feature::Duplicates);
        assert_eq!(app.job.as_ref().unwrap().ctx.feature, Feature::Duplicates);
        assert_eq!(app.job.as_ref().unwrap().name, "Scanning");
        loop {
            let event = app.rx.recv_timeout(Duration::from_secs(10)).unwrap();
            match event {
                Event::Report(report) if report.feature == Feature::Duplicates => {
                    assert_eq!(report.files.len(), 2);
                    assert!(
                        report
                            .files
                            .iter()
                            .all(|f| common::inside(&f.path, &fixture))
                    );
                    break;
                }
                Event::Finished(Err(error)) => panic!("Scan failed: {error}"),
                Event::Finished(Ok(_)) => panic!("Scan ended without results"),
                _ => {}
            }
        }
        let _ = app.update(Message::TrayAction(Box::new(Message::Navigate(
            Feature::Drive,
        ))));
        assert_eq!(app.page, Feature::Drive);
        assert_eq!(app.job.as_ref().unwrap().ctx.feature, Feature::Duplicates);
        std::fs::remove_file(fixture.join("a.txt")).unwrap();
        std::fs::remove_file(fixture.join("b.txt")).unwrap();
        std::fs::remove_dir(fixture).unwrap();
    }

    #[test]
    fn duplicate_expand_all_toggles_both_levels_without_changing_selection() {
        let (mut app, _) = App::new(Config {
            preview: true,
            data: Some(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!(
                "../../validation-output/duplicate-expand-test-{}",
                std::process::id()
            ))),
            ..Default::default()
        });
        app.page = Feature::Duplicates;
        let mut report = ScanResult {
            feature: Feature::Duplicates,
            ..Default::default()
        };
        for (group, name) in [("zip-group", "a.zip"), ("setup-group", "a.exe")] {
            report.categories.push(Category {
                id: group.into(),
                file_ids: vec![name.into()],
                ..Default::default()
            });
            report.files.push(FileEntry {
                id: name.into(),
                path: PathBuf::from(r"C:\Downloads").join(name),
                category: group.into(),
                size: 10,
                allocated: 10,
                identity: Default::default(),
                scope: "scope".into(),
                blocked: String::new(),
            });
        }
        let expected: BTreeSet<String> = [
            "duplicate-type:archives",
            "zip-group",
            "duplicate-type:installers",
            "setup-group",
        ]
        .into_iter()
        .map(String::from)
        .collect();
        assert_eq!(ui::pages::duplicate_expansion_ids(&report), expected);
        app.reports.insert(Feature::Duplicates, Arc::new(report));
        app.selected
            .insert(Feature::Duplicates, ["chosen".into()].into_iter().collect());
        app.expanded = ["archives".into(), "duplicate-type:archives".into()]
            .into_iter()
            .collect();
        let _ = app.update(Message::DuplicateExpandAll);
        assert!(expected.is_subset(&app.expanded));
        let _ = app.update(Message::DuplicateExpandAll);
        assert_eq!(app.expanded, ["archives".into()].into_iter().collect());
        assert_eq!(
            app.choices(Feature::Duplicates),
            ["chosen".into()].into_iter().collect()
        );
        let _ = app.update(Message::DuplicateExpandAll);
        assert!(expected.is_subset(&app.expanded));
    }

    #[test]
    fn changing_duplicate_scan_options_keeps_existing_results_and_selection() {
        let data = std::env::current_dir().unwrap().join(format!(
            "../../validation-output/duplicate-options-test-{}",
            std::process::id()
        ));
        let (mut app, _) = App::new(Config {
            preview: true,
            data: Some(data),
            ..Default::default()
        });
        app.page = Feature::Duplicates;
        app.disks = vec![Disk {
            root: "D:\\".into(),
            total: 0,
            free: 0,
            used: 0,
            filesystem: "NTFS".into(),
        }];
        let report = Arc::new(ScanResult {
            feature: Feature::Duplicates,
            token: "C-drive-scan".into(),
            ..Default::default()
        });
        app.reports.insert(Feature::Duplicates, report.clone());
        app.selected
            .insert(Feature::Duplicates, ["copy".into()].into_iter().collect());
        app.scan_elapsed.insert(Feature::Duplicates, 1200);
        let _ = app.update(Message::DuplicateDrive("D:\\".into()));
        let _ = app.update(Message::DuplicateMinimum("1 MB".into()));
        assert_eq!(app.duplicate_drive, "D:\\");
        assert_eq!(app.duplicate_minimum, 2);
        assert!(Arc::ptr_eq(&app.reports[&Feature::Duplicates], &report));
        assert!(app.choices(Feature::Duplicates).contains("copy"));
        assert_eq!(app.scan_elapsed[&Feature::Duplicates], 1200);
        app.invalidate_duplicates();
        assert!(!app.reports.contains_key(&Feature::Duplicates));
        assert!(app.choices(Feature::Duplicates).is_empty());
    }

    #[test]
    fn bulk_selection_matches_checkboxes_before_and_after_scan() {
        let mut report = ScanResult {
            categories: vec![
                Category {
                    id: "empty".into(),
                    ..Default::default()
                },
                Category {
                    id: "found".into(),
                    count: 1,
                    ..Default::default()
                },
                Category {
                    id: "blocked".into(),
                    count: 1,
                    view_only: true,
                    ..Default::default()
                },
            ],
            ..Default::default()
        };
        let ids = |r: &ScanResult| {
            r.categories
                .iter()
                .filter(|c| category_selectable(r, c))
                .map(|c| c.id.as_str())
                .collect::<Vec<_>>()
                .join(",")
        };
        assert_eq!(ids(&report), "empty,found");
        report.token = "scanned".into();
        assert_eq!(ids(&report), "found");
    }
}
