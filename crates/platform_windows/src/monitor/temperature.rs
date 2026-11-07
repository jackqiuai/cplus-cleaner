use serde::Deserialize;
use std::{
    process::Stdio,
    time::{Duration, Instant},
};

#[derive(Clone, Copy, Debug, Default, Deserialize)]
pub struct Temperatures {
    pub cpu: Option<f64>,
    pub disk: Option<f64>,
}
impl Temperatures {
    pub fn fresh(self, sampled: Option<Instant>) -> Self {
        if sampled.is_some_and(|at| at.elapsed() < Duration::from_secs(30)) {
            self
        } else {
            Self::default()
        }
    }
}

pub fn read() -> Temperatures {
    read_inner().unwrap_or_default()
}
fn read_inner() -> Option<Temperatures> {
    let executable = std::path::PathBuf::from(std::env::var_os("SystemRoot")?)
        .join("System32/WindowsPowerShell/v1.0/powershell.exe");
    let mut child = crate::processes::hidden(executable)
        .args([
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            include_str!("temperature.ps1"),
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let start = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if start.elapsed() < Duration::from_secs(10) => {
                std::thread::sleep(Duration::from_millis(50))
            }
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
    let output = child.wait_with_output().ok()?;
    if !output.status.success() {
        return None;
    }
    decode(&output.stdout)
}
fn decode(bytes: &[u8]) -> Option<Temperatures> {
    let mut value: Temperatures = serde_json::from_slice(bytes).ok()?;
    for v in [&mut value.cpu, &mut value.disk] {
        *v = v.filter(|n| n.is_finite() && *n > 0.0 && *n <= 125.0);
    }
    Some(value)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_read_has_a_deadline_and_only_returns_supported_values() {
        let start = Instant::now();
        let value = read();
        assert!(start.elapsed() < Duration::from_secs(12));
        for v in [value.cpu, value.disk].into_iter().flatten() {
            assert!(v.is_finite() && v > 0.0 && v <= 125.0);
        }
        eprintln!(
            "CPU temperature: {:?}; system-disk temperature: {:?}",
            value.cpu, value.disk
        );
    }
    #[test]
    fn invalid_and_stale_values_remain_unavailable() {
        let value = decode(br#"{"cpu":56.5,"disk":42}"#).unwrap();
        assert_eq!(value.fresh(Some(Instant::now())).cpu, Some(56.5));
        assert!(
            value
                .fresh(Some(Instant::now() - Duration::from_secs(31)))
                .cpu
                .is_none()
        );
        let invalid = decode(br#"{"cpu":0,"disk":65535}"#).unwrap();
        assert!(invalid.cpu.is_none() && invalid.disk.is_none());
        assert!(decode(b"not JSON").is_none());
        assert!(
            decode(br#"{"cpu":null,"disk":null}"#)
                .unwrap()
                .cpu
                .is_none()
        );
    }
}
