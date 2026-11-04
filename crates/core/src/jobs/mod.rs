use crate::Feature;
use serde::{Deserialize, Serialize};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};
use std::time::{Duration, Instant};

#[derive(Clone, Default)]
pub struct Cancel(Arc<AtomicBool>);
impl Cancel {
    pub fn cancel(&self) {
        self.0.store(true, Ordering::Relaxed);
    }
    pub fn cancelled(&self) -> bool {
        self.0.load(Ordering::Relaxed)
    }
    pub fn check(&self) -> anyhow::Result<()> {
        if self.cancelled() {
            anyhow::bail!("Operation cancelled")
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Progress {
    pub feature: Feature,
    pub phase: String,
    pub current: String,
    pub done: u64,
    pub total: Option<u64>,
    pub bytes: u64,
    pub elapsed_ms: u64,
    #[serde(default)]
    pub matched: Option<u64>,
}
#[derive(Clone)]
pub struct Context {
    pub cancel: Cancel,
    pub feature: Feature,
    started: Instant,
    progress: Arc<Mutex<Progress>>,
    last: Arc<Mutex<Instant>>,
}
impl Context {
    pub fn new(feature: Feature) -> Self {
        Self {
            cancel: Cancel::default(),
            feature,
            started: Instant::now(),
            progress: Arc::new(Mutex::new(Progress {
                feature,
                ..Default::default()
            })),
            last: Arc::new(Mutex::new(Instant::now() - Duration::from_secs(1))),
        }
    }
    pub fn child(&self, feature: Feature) -> Self {
        Self {
            feature,
            started: Instant::now(),
            ..self.clone()
        }
    }
    pub fn snapshot(&self) -> Progress {
        let mut p = self
            .progress
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        p.elapsed_ms = self.started.elapsed().as_millis() as u64;
        p
    }
    pub fn set(
        &self,
        phase: &str,
        current: impl Into<String>,
        done: u64,
        total: Option<u64>,
        bytes: u64,
    ) {
        let mut last = self.last.lock().unwrap_or_else(|e| e.into_inner());
        if last.elapsed() < Duration::from_millis(60)
            && Some(done) != total
            && self
                .progress
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .phase
                == phase
        {
            return;
        }
        *last = Instant::now();
        *self.progress.lock().unwrap_or_else(|e| e.into_inner()) = Progress {
            feature: self.feature,
            phase: phase.into(),
            current: current.into(),
            done,
            total,
            bytes,
            elapsed_ms: self.started.elapsed().as_millis() as u64,
            matched: None,
        };
    }
    /// Publish traversal progress even when no candidate matches. Format the
    /// path only after throttling, so large directory walks stay inexpensive.
    pub fn scan_progress(
        &self,
        path: &std::path::Path,
        checked: u64,
        matched: u64,
        bytes: u64,
        force: bool,
    ) {
        let mut last = self.last.lock().unwrap_or_else(|e| e.into_inner());
        if !force && last.elapsed() < Duration::from_millis(100) {
            return;
        }
        *last = Instant::now();
        *self.progress.lock().unwrap_or_else(|e| e.into_inner()) = Progress {
            feature: self.feature,
            phase: "scan".into(),
            current: crate::common::display_path(path),
            done: checked,
            total: None,
            bytes,
            elapsed_ms: self.elapsed_ms(),
            matched: Some(matched),
        };
    }
    pub fn elapsed_ms(&self) -> u64 {
        self.started.elapsed().as_millis() as u64
    }
    pub fn path_progress(
        &self,
        phase: &str,
        path: &std::path::Path,
        done: u64,
        bytes: u64,
        force: bool,
    ) {
        let mut last = self.last.lock().unwrap_or_else(|e| e.into_inner());
        if !force && last.elapsed() < Duration::from_millis(100) {
            return;
        }
        *last = Instant::now();
        *self.progress.lock().unwrap_or_else(|e| e.into_inner()) = Progress {
            feature: self.feature,
            phase: phase.into(),
            current: crate::common::display_path(path),
            done,
            total: None,
            bytes,
            elapsed_ms: self.elapsed_ms(),
            matched: None,
        };
    }
}
