//! One shared read-only model for the native taskbar and Iced detail window.
use cplus_platform_windows::{
    monitor::{
        Sample, Sampler,
        taskbar::{Panel, Status},
    },
    shell,
};
use iced::widget::image;
use std::{
    collections::{HashMap, VecDeque},
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Tab {
    #[default]
    Overview,
    Cpu,
    Memory,
    Disk,
    Network,
}
impl Tab {
    pub const ALL: [Self; 5] = [
        Self::Overview,
        Self::Cpu,
        Self::Memory,
        Self::Disk,
        Self::Network,
    ];
    pub fn key(self) -> &'static str {
        match self {
            Self::Overview => "Overview",
            Self::Cpu => "CPU",
            Self::Memory => "Memory",
            Self::Disk => "Disk",
            Self::Network => "Network",
        }
    }
}
#[derive(Clone, Debug)]
pub struct Point {
    pub at: Instant,
    pub cpu: Option<f64>,
    pub read: Option<f64>,
    pub write: Option<f64>,
    pub up: Option<f64>,
    pub down: Option<f64>,
}
type Icons = HashMap<PathBuf, Option<image::Handle>>;
#[derive(Default)]
struct Shared {
    sequence: u64,
    sample: Sample,
    icons: Icons,
}
pub struct Monitor {
    pub panel: Option<Panel>,
    shared: Arc<Mutex<Shared>>,
    enabled: Arc<AtomicBool>,
    details: Arc<AtomicBool>,
    network_details: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    sequence: u64,
    pub sample: Sample,
    pub icons: Icons,
    pub points: VecDeque<Point>,
    pub tab: Tab,
    pub window: Option<iced::window::Id>,
    pub focused: bool,
    pub status: Status,
    pub updated: Option<Instant>,
}
impl Monitor {
    pub fn new(enabled: bool, preview: bool) -> Self {
        let shared = Arc::new(Mutex::new(Shared::default()));
        let enabled = Arc::new(AtomicBool::new(enabled));
        let details = Arc::new(AtomicBool::new(false));
        let network_details = Arc::new(AtomicBool::new(false));
        let stop = Arc::new(AtomicBool::new(false));
        // Preview windows never attach to the user's real taskbar.
        let panel = (!preview).then(Panel::default);
        let panel_model = panel.as_ref().map(|p| p.model.clone());
        let (state, active, detailed, network_detailed, quit) = (
            shared.clone(),
            enabled.clone(),
            details.clone(),
            network_details.clone(),
            stop.clone(),
        );
        std::thread::spawn(move || {
            let mut sampler = None;
            let mut icons = Icons::new();
            while !quit.load(Ordering::Relaxed) {
                if active.load(Ordering::Relaxed) {
                    let reader = sampler.get_or_insert_with(Sampler::default);
                    let sample = reader.sample(
                        detailed.load(Ordering::Relaxed),
                        network_detailed.load(Ordering::Relaxed),
                    );
                    // Only visible high-usage entries need icons; never load every EXE.
                    let mut cpu: Vec<_> = sample.processes.iter().collect();
                    cpu.sort_by(|a, b| b.cpu.unwrap_or(-1.0).total_cmp(&a.cpu.unwrap_or(-1.0)));
                    let mut memory = cpu.clone();
                    memory.sort_by_key(|p| std::cmp::Reverse(p.memory));
                    let mut budget = 4;
                    for p in cpu.into_iter().take(32).chain(memory.into_iter().take(32)) {
                        if !p.path.as_os_str().is_empty()
                            && !icons.contains_key(&p.path)
                            && budget > 0
                        {
                            let icon = shell::icon(&p.path, 0)
                                .map(|i| image::Handle::from_rgba(i.width, i.height, i.rgba));
                            icons.insert(p.path.clone(), icon);
                            budget -= 1;
                        }
                    }
                    if icons.len() > 256 {
                        icons.retain(|path, _| sample.processes.iter().any(|p| p.path == *path));
                    }
                    if let Some(model) = &panel_model {
                        let mut summary = sample.clone();
                        summary.processes.clear();
                        if let Ok(mut m) = model.try_lock() {
                            m.sample = summary;
                        }
                    }
                    let mut output = state.lock().unwrap_or_else(|e| e.into_inner());
                    output.sequence = output.sequence.wrapping_add(1);
                    output.sample = sample;
                    output.icons = icons.clone();
                } else {
                    if let Some(reader) = &mut sampler {
                        reader.pause();
                    }
                }
                for _ in 0..10 {
                    if quit.load(Ordering::Relaxed) {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
            }
        });
        Self {
            panel,
            shared,
            enabled,
            details,
            network_details,
            stop,
            sequence: 0,
            sample: Sample::default(),
            icons: Icons::new(),
            points: VecDeque::new(),
            tab: Tab::Overview,
            window: None,
            focused: false,
            status: Status::Disabled,
            updated: None,
        }
    }
    pub fn enable(&mut self, enabled: bool) {
        self.enabled.store(enabled, Ordering::Relaxed);
        if !enabled {
            self.points.clear();
            self.sample = Sample::default();
            self.updated = None;
        }
        if let Some(panel) = &self.panel {
            panel.set_enabled(enabled);
        }
    }
    pub fn poll(&mut self) {
        self.network_details.store(
            self.window.is_some() && self.tab == Tab::Network,
            Ordering::Relaxed,
        );
        self.details.store(
            self.window.is_some() && matches!(self.tab, Tab::Cpu | Tab::Memory),
            Ordering::Relaxed,
        );
        if let Some(panel) = &self.panel {
            // Start shell discovery only once the application's event loop is running.
            panel.set_enabled(self.enabled.load(Ordering::Relaxed));
            if let Ok(model) = panel.model.try_lock() {
                self.status = model.status;
            }
        } else {
            self.status = Status::Disabled;
        }
        if !self.enabled.load(Ordering::Relaxed) {
            return;
        }
        let Ok(state) = self.shared.try_lock() else {
            return;
        };
        if state.sequence == self.sequence {
            return;
        }
        if self
            .updated
            .is_some_and(|at| at.elapsed() > Duration::from_secs(5))
        {
            self.points.clear();
        }
        self.sequence = state.sequence;
        self.sample = state.sample.clone();
        self.icons = state.icons.clone();
        self.updated = Some(Instant::now());
        let n = self.sample.network.as_ref();
        self.points.push_back(Point {
            at: Instant::now(),
            cpu: self.sample.cpu,
            read: self.sample.read,
            write: self.sample.write,
            up: n.and_then(|n| n.upload),
            down: n.and_then(|n| n.download),
        });
        while self.points.len() > 60
            || self
                .points
                .front()
                .is_some_and(|p| p.at.elapsed() >= Duration::from_secs(60))
        {
            self.points.pop_front();
        }
    }
    pub fn stale(&self) -> bool {
        self.updated
            .is_none_or(|at| at.elapsed() > Duration::from_secs(5))
    }
}
impl Drop for Monitor {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(panel) = &self.panel {
            panel.set_enabled(false);
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn network_page_receives_the_connected_wlan_name_from_the_worker() {
        let Some(expected) = Sampler::default().sample(false, true).network else {
            return;
        };
        if expected.wlan_name.is_empty() {
            return;
        }
        let mut monitor = Monitor::new(true, true);
        monitor.window = Some(iced::window::Id::unique());
        monitor.tab = Tab::Network;
        let start = Instant::now();
        while start.elapsed() < Duration::from_secs(4) {
            monitor.poll();
            if let Some(network) = &monitor.sample.network
                && network.wlan_name == expected.wlan_name
            {
                assert_eq!(network.name, expected.name);
                assert!(monitor.sample.processes.is_empty());
                return;
            }
            std::thread::sleep(Duration::from_millis(30));
        }
        panic!("Network page did not receive the connected WLAN name from the worker");
    }
    #[test]
    fn network_page_requests_wlan_details_without_enumerating_processes() {
        let mut monitor = Monitor::new(false, true);
        monitor.window = Some(iced::window::Id::unique());
        for tab in Tab::ALL {
            monitor.tab = tab;
            monitor.poll();
            assert_eq!(
                monitor.network_details.load(Ordering::Relaxed),
                tab == Tab::Network
            );
            assert_eq!(
                monitor.details.load(Ordering::Relaxed),
                matches!(tab, Tab::Cpu | Tab::Memory)
            );
        }
        monitor.window = None;
        monitor.poll();
        assert!(!monitor.network_details.load(Ordering::Relaxed));
        assert!(!monitor.details.load(Ordering::Relaxed));
    }
    #[test]
    fn busy_sampling_cannot_block_the_ui_or_the_switch() {
        let mut monitor = Monitor::new(false, true);
        let shared = monitor.shared.clone();
        let _held = shared.lock().unwrap();
        let start = Instant::now();
        monitor.enable(true);
        monitor.poll();
        monitor.enable(false);
        assert!(start.elapsed() < Duration::from_millis(100));
        assert!(!monitor.enabled.load(Ordering::Relaxed));
    }
}
