use crate::{common, jobs::Context, *};
use anyhow::{Context as _, Result, bail};
use cplus_platform_windows::{
    processes,
    registry::{HKEY_LOCAL_MACHINE, Key},
    shell, wide,
};
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
    process::Stdio,
    time::{Duration, Instant},
};
use windows_sys::Win32::{Foundation::*, Storage::FileSystem::*, UI::Shell::*};

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Action {
    EmptyRecycleBin,
    HibernateOff,
    HibernateOn,
    PagefileSettings,
    AnalyzeComponents,
    CleanupComponents,
    ClearDeliveryOptimization,
    ClearWindowsUpdate,
    ClearPreviousWindows,
}
pub fn locked_file_size(path: &Path) -> Option<u64> {
    let mut data: WIN32_FIND_DATAW = unsafe { std::mem::zeroed() };
    let handle = unsafe { FindFirstFileW(wide(path).as_ptr(), &mut data) };
    if handle == INVALID_HANDLE_VALUE {
        return None;
    }
    unsafe {
        FindClose(handle);
    }
    Some((data.nFileSizeHigh as u64) << 32 | data.nFileSizeLow as u64)
}
pub fn scan(ctx: &Context) -> Result<ScanResult> {
    ctx.set("system", "", 0, None, 0);
    let root = common::system_drive();
    let recycle = unsafe {
        let mut info: SHQUERYRBINFO = std::mem::zeroed();
        info.cbSize = std::mem::size_of::<SHQUERYRBINFO>() as u32;
        let status = SHQueryRecycleBinW(wide(&root).as_ptr(), &mut info);
        if status >= 0 {
            Some(info.i64Size.max(0) as u64)
        } else {
            None
        }
    };
    let hibernate = locked_file_size(&root.join("hiberfil.sys"));
    let pagefile = locked_file_size(&root.join("pagefile.sys"));
    let swap = locked_file_size(&root.join("swapfile.sys"));
    let mut items = vec![];
    for (id, bytes) in [
        ("recycle_bin", recycle),
        ("hibernation", hibernate),
        ("pagefile", pagefile),
        ("swapfile", swap),
        ("components", None),
    ] {
        items.push(SystemItem {
            id: id.into(),
            bytes,
            enabled: bytes.is_some_and(|n| n > 0),
            message: String::new(),
        });
    }
    if pagefile.is_none()
        && let Ok(key) = Key::open(
            HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management",
            0,
            false,
        )
        && key.raw("PagingFiles").is_ok()
    {
        items[2].message = "managed_by_windows".into();
    }
    Ok(ScanResult {
        feature: Feature::System,
        token: common::token(),
        system: items,
        ..Default::default()
    })
}
#[derive(Serialize, Deserialize)]
struct Request {
    action: Action,
    created: i64,
    nonce: String,
}
fn system_exe(name: &str) -> PathBuf {
    common::env_path("SystemRoot", r"C:\Windows".into())
        .join("System32")
        .join(name)
}
fn run_command(exe: &Path, args: &[&str], ctx: &Context) -> Result<String> {
    let mut child = processes::hidden(exe)
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    let stdout = child.stdout.take().context("Missing system output")?;
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).split(b'\n') {
            match line {
                Ok(line) => {
                    let _ = tx.send(String::from_utf8_lossy(&line).into_owned());
                }
                Err(_) => break,
            }
        }
    });
    let percent = regex::Regex::new(r"(\d{1,3})(?:\.\d+)?%").unwrap();
    let mut output = String::new();
    let start = Instant::now();
    loop {
        for line in rx.try_iter() {
            if let Some(n) = percent
                .captures(&line)
                .and_then(|c| c[1].parse::<u64>().ok())
            {
                ctx.set("system", "", n.min(99), Some(100), 0);
            }
            if output.len() < 100_000 {
                output.push_str(&line);
                output.push('\n');
            }
        }
        if let Some(code) = child.try_wait()? {
            for line in rx.try_iter() {
                if output.len() < 100_000 {
                    output.push_str(&line);
                    output.push('\n');
                }
            }
            if !code.success() && code.code() != Some(3010) {
                bail!("Windows operation returned {}", code.code().unwrap_or(-1));
            }
            ctx.set("system", "", 100, Some(100), 0);
            return Ok(output);
        }
        if start.elapsed() > Duration::from_secs(7200) {
            bail!("Windows servicing is still running; wait before starting another operation");
        }
        std::thread::sleep(Duration::from_millis(180));
    }
}
fn execute(action: Action, ctx: &Context) -> Result<String> {
    match action {
        Action::ClearDeliveryOptimization
        | Action::ClearWindowsUpdate
        | Action::ClearPreviousWindows => windows_cache(action, true, ctx),
        Action::PagefileSettings => {
            shell::open(
                system_exe("SystemPropertiesAdvanced.exe")
                    .to_str()
                    .context("Invalid Windows path")?,
            )?;
            Ok(String::new())
        }
        Action::EmptyRecycleBin => {
            let hr = unsafe {
                SHEmptyRecycleBinW(
                    std::ptr::null_mut(),
                    wide(common::system_drive()).as_ptr(),
                    SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND,
                )
            };
            if hr < 0 {
                bail!("Windows could not empty the recycle bin ({hr:#x})");
            }
            Ok(String::new())
        }
        Action::HibernateOff => run_command(&system_exe("powercfg.exe"), &["/h", "off"], ctx),
        Action::HibernateOn => run_command(&system_exe("powercfg.exe"), &["/h", "on"], ctx),
        Action::AnalyzeComponents => run_command(
            &system_exe("dism.exe"),
            &[
                "/Online",
                "/Cleanup-Image",
                "/AnalyzeComponentStore",
                "/English",
            ],
            ctx,
        ),
        Action::CleanupComponents => run_command(
            &system_exe("dism.exe"),
            &[
                "/Online",
                "/Cleanup-Image",
                "/StartComponentCleanup",
                "/English",
            ],
            ctx,
        ),
    }
}
pub(crate) fn windows_cache(action: Action, purge: bool, ctx: &Context) -> Result<String> {
    let kind = match action {
        Action::ClearDeliveryOptimization => "Delivery Optimization Files",
        Action::ClearWindowsUpdate => "Update Cleanup",
        Action::ClearPreviousWindows => "Previous Installations",
        _ => bail!("Unsupported Windows cleanup handler"),
    };
    windows_cache_script(kind, if purge { "purge" } else { "measure" }, ctx)
}
pub(crate) fn windows_cache_batch(ctx: &Context) -> Result<serde_json::Value> {
    serde_json::from_str(&windows_cache_script("batch", "measure", ctx)?).map_err(Into::into)
}
fn windows_cache_script(kind: &str, mode: &str, ctx: &Context) -> Result<String> {
    let script = include_str!("../../../../../resources/windows/disk-cleanup.ps1")
        .replace("__KIND__", kind)
        .replace("__MODE__", mode);
    run_command(
        &system_exe("WindowsPowerShell/v1.0/powershell.exe"),
        &["-NoProfile", "-NonInteractive", "-STA", "-Command", &script],
        ctx,
    )
}
pub fn perform(store: &Store, action: Action, ctx: &Context) -> Result<String> {
    if store.isolated {
        bail!("System changes are disabled in preview mode");
    }
    let output = if matches!(action, Action::PagefileSettings | Action::EmptyRecycleBin)
        || shell::is_admin()
    {
        execute(action, ctx)?
    } else {
        let nonce = common::token();
        let relative = format!("system-jobs/{nonce}.json");
        let file = store.path(&relative)?;
        store.write(
            &relative,
            &Request {
                action,
                created: chrono::Utc::now().timestamp(),
                nonce,
            },
        )?;
        let args = format!("--system-job \"{}\"", file.display());
        let handle = shell::elevate(&std::env::current_exe()?, &args)?;
        let exit = loop {
            match handle.poll() {
                Ok(Some(exit)) => {
                    break exit;
                }
                Ok(None) => {}
                Err(error) => {
                    return Err(error.into());
                }
            }
            if let Ok(bytes) = fs::read(file.with_extension("progress.json"))
                && let Ok(p) = serde_json::from_slice::<crate::jobs::Progress>(&bytes)
            {
                ctx.set("system", p.current, p.done, p.total, p.bytes);
            }
            std::thread::sleep(Duration::from_millis(180));
        };
        let response = file.with_extension("result.json");
        let content = fs::read(&response)
            .context("Windows permission was not granted or the helper did not finish")?;
        let value: serde_json::Value = serde_json::from_slice(&content)?;
        let _ = fs::remove_file(&response);
        let _ = fs::remove_file(&file);
        let _ = fs::remove_file(file.with_extension("progress.json"));
        if exit != 0 || value["ok"] != true {
            bail!(
                "{}",
                value["error"]
                    .as_str()
                    .unwrap_or("Windows operation failed")
            );
        }
        value["output"].as_str().unwrap_or("").into()
    };
    let _ = store.append("system_action", &format!("{action:?}"), 1, 0, true);
    Ok(output)
}
pub fn elevated_job(path: &Path) -> Result<()> {
    let store = Store::new(None, false)?;
    let parent = store.root.join("system-jobs");
    common::no_links(path)?;
    if !path.is_absolute() || path.parent() != Some(parent.as_path()) || !shell::is_admin() {
        bail!("Invalid system job");
    }
    let request: Request = serde_json::from_slice(&fs::read(path)?)?;
    if path.file_stem().and_then(|s| s.to_str()) != Some(&request.nonce)
        || request.nonce.len() != 32
        || (chrono::Utc::now().timestamp() - request.created).abs() > 600
    {
        bail!("Expired system job");
    }
    let ctx = Context::new(Feature::System);
    let progress = ctx.clone();
    let file = path.with_extension("progress.json");
    let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let stopped = stop.clone();
    let writer = std::thread::spawn(move || {
        while !stopped.load(std::sync::atomic::Ordering::Relaxed) {
            let _ = crate::storage::atomic_write(
                &file,
                &serde_json::to_vec(&progress.snapshot()).unwrap_or_default(),
            );
            std::thread::sleep(Duration::from_millis(200));
        }
    });
    let result = execute(request.action, &ctx);
    stop.store(true, std::sync::atomic::Ordering::Relaxed);
    let _ = writer.join();
    let value = match &result {
        Ok(output) => serde_json::json!({"ok":true,"output":output}),
        Err(e) => serde_json::json!({"ok":false,"error":e.to_string()}),
    };
    crate::storage::atomic_write(
        &path.with_extension("result.json"),
        &serde_json::to_vec(&value)?,
    )?;
    result.map(|_| ())
}

pub fn configure_startup(settings: &Settings) -> Result<()> {
    let key = Key::create(
        cplus_platform_windows::registry::HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
    )?;
    if settings.autostart {
        key.set_string(
            "CPlusCleaner",
            &format!("\"{}\" --background", std::env::current_exe()?.display()),
        )?;
    } else {
        let _ = key.delete_value("CPlusCleaner");
    }
    let legacy = key.string("CCCLEANER").to_lowercase();
    if ["cpluscleaner", "ccleanner", "cccleaner"]
        .iter()
        .any(|name| legacy.contains(name))
    {
        key.delete_value("CCCLEANER")?;
    }
    Ok(())
}
pub fn configure_schedule(settings: &Settings) -> Result<()> {
    settings.validate()?;
    let exe = system_exe("schtasks.exe");
    let output = if settings.scheduled_cleanup {
        processes::hidden(&exe)
            .args([
                "/Create",
                "/F",
                "/IT",
                "/SC",
                "DAILY",
                "/TN",
                "CPlusCleaner-DailyCleanup",
                "/ST",
                &settings.cleanup_time,
                "/TR",
                &format!(
                    "\"{}\" --scheduled-cleanup",
                    std::env::current_exe()?.display()
                ),
            ])
            .output()?
    } else {
        processes::hidden(&exe)
            .args(["/Delete", "/F", "/TN", "CPlusCleaner-DailyCleanup"])
            .output()?
    };
    if settings.scheduled_cleanup && !output.status.success() {
        bail!("Windows could not save the scheduled cleanup task");
    }
    if !settings.scheduled_cleanup
        && !output.status.success()
        && processes::hidden(&exe)
            .args(["/Query", "/TN", "CPlusCleaner-DailyCleanup"])
            .output()?
            .status
            .success()
    {
        bail!("Windows could not disable the scheduled cleanup task");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn windows_cleanup_never_runs_or_logs_changes_in_preview() {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::new(Some(temp.path().into()), true).unwrap();
        for action in [
            Action::ClearDeliveryOptimization,
            Action::ClearWindowsUpdate,
            Action::ClearPreviousWindows,
            Action::CleanupComponents,
        ] {
            assert!(
                perform(&store, action, &Context::new(Feature::Drive))
                    .unwrap_err()
                    .to_string()
                    .contains("disabled in preview")
            );
        }
        assert!(store.activities().is_empty());
        assert!(!temp.path().join("system-jobs").exists());
    }
}
