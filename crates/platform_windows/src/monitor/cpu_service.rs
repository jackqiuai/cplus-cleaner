//! A fixed read-only service. Local clients read an administrator-protected snapshot;
//! there is deliberately no IPC command that can execute register operations.
use super::cpu_sensor::{self, Reader};
use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use std::{
    io::{Read, Write},
    path::{Path, PathBuf},
    ptr,
    sync::atomic::{AtomicBool, AtomicUsize, Ordering},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use windows_sys::Win32::{Foundation::*, System::Services::*, UI::Shell::*};
pub const NAME: &str = "CPlusCleanerCpuTemperature";
static STOP: AtomicBool = AtomicBool::new(false);
static STATUS: AtomicUsize = AtomicUsize::new(0);
#[derive(Serialize, Deserialize)]
struct Snapshot {
    schema: u32,
    sampled_ms: u64,
    celsius: Option<f64>,
    error: String,
}
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}
fn directory() -> Result<PathBuf> {
    Ok(cpu_sensor::folder(&FOLDERID_ProgramData)?.join(NAME))
}
fn installation() -> Result<PathBuf> {
    Ok(cpu_sensor::folder(&FOLDERID_ProgramFiles)?.join(NAME))
}
pub fn cached() -> Option<f64> {
    let binary = installation().ok()?.join("CPlusCleanerTemperature.exe");
    cpu_sensor::no_links(&binary).ok()?;
    if !binary.is_file() {
        return None;
    }
    let path = directory().ok()?.join("temperature.json");
    cpu_sensor::no_links(&path).ok()?;
    let bytes = bounded_snapshot(&path)?;
    let sample: Snapshot = serde_json::from_slice(&bytes).ok()?;
    validate(&sample, now())
}
fn bounded_snapshot(path: &Path) -> Option<Vec<u8>> {
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .ok()?
        .take(4097)
        .read_to_end(&mut bytes)
        .ok()?;
    (bytes.len() <= 4096).then_some(bytes)
}
fn validate(sample: &Snapshot, time: u64) -> Option<f64> {
    if sample.schema != 1
        || sample.sampled_ms > time.saturating_add(2000)
        || time.saturating_sub(sample.sampled_ms) > 12_000
    {
        return None;
    }
    sample
        .celsius
        .filter(|v| v.is_finite() && *v > 0.0 && *v <= 125.0)
}
fn snapshot(celsius: Option<f64>, error: String) -> Result<()> {
    let root = directory()?;
    cpu_sensor::no_links(&root)?;
    let dest = root.join("temperature.json");
    cpu_sensor::no_links(&dest)?;
    atomic_file(
        &dest,
        &serde_json::to_vec(&Snapshot {
            schema: 1,
            sampled_ms: now(),
            celsius,
            error,
        })?,
        true,
    )?;
    Ok(())
}
fn atomic_file(dest: &Path, bytes: &[u8], protected: bool) -> Result<()> {
    cpu_sensor::no_links(dest)?;
    let temp = dest.with_extension(format!("{}-{}.new", std::process::id(), now()));
    let mut file = if protected {
        protected_file(&temp)?
    } else {
        std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)?
    };
    let written = file.write_all(bytes).and_then(|()| file.sync_all());
    drop(file);
    let result = (|| -> Result<()> {
        written?;
        retry_file(|| {
            let ok = unsafe {
                windows_sys::Win32::Storage::FileSystem::MoveFileExW(
                    crate::wide(&temp).as_ptr(),
                    crate::wide(dest).as_ptr(),
                    windows_sys::Win32::Storage::FileSystem::MOVEFILE_REPLACE_EXISTING,
                )
            };
            if ok != 0 {
                Ok(())
            } else {
                Err(std::io::Error::last_os_error())
            }
        })
        .with_context(|| format!("Cannot replace temperature component {}", dest.display()))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(temp);
    }
    result
}
fn retry_file<T>(mut operation: impl FnMut() -> std::io::Result<T>) -> std::io::Result<T> {
    let start = std::time::Instant::now();
    loop {
        match operation() {
            Err(error)
                if matches!(error.raw_os_error(), Some(5 | 32 | 33))
                    && start.elapsed() < Duration::from_secs(5) =>
            {
                std::thread::sleep(Duration::from_millis(100))
            }
            result => return result,
        }
    }
}
fn protected_file(path: &Path) -> Result<std::fs::File> {
    use std::os::windows::io::FromRawHandle;
    use windows_sys::Win32::{Security::Authorization::*, Storage::FileSystem::*};
    // Set the owner and protected DACL at creation, before publishing an executable.
    // A filtered administrator must not inherit ownership of a privileged service binary.
    let owner = if STATUS.load(Ordering::Relaxed) == 0 {
        "BA"
    } else {
        "SY"
    };
    let sddl = crate::wide(format!("O:{owner}D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;BU)"));
    let mut descriptor = ptr::null_mut();
    ensure!(
        unsafe {
            ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl.as_ptr(),
                1,
                &mut descriptor,
                ptr::null_mut(),
            )
        } != 0,
        "Cannot construct service file permissions"
    );
    let attributes = windows_sys::Win32::Security::SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<windows_sys::Win32::Security::SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor,
        bInheritHandle: 0,
    };
    let handle = unsafe {
        CreateFileW(
            crate::wide(path).as_ptr(),
            GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_DELETE,
            &attributes,
            CREATE_NEW,
            FILE_ATTRIBUTE_NORMAL,
            ptr::null_mut(),
        )
    };
    let error = std::io::Error::last_os_error();
    unsafe {
        LocalFree(descriptor);
    }
    if handle == INVALID_HANDLE_VALUE {
        return Err(error.into());
    }
    Ok(unsafe { std::fs::File::from_raw_handle(handle) })
}
fn state(current: u32) {
    let handle = STATUS.load(Ordering::Relaxed) as SERVICE_STATUS_HANDLE;
    if handle.is_null() {
        return;
    }
    let value = SERVICE_STATUS {
        dwServiceType: SERVICE_WIN32_OWN_PROCESS,
        dwCurrentState: current,
        dwControlsAccepted: if current == SERVICE_RUNNING {
            SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN
        } else {
            0
        },
        dwWin32ExitCode: 0,
        dwServiceSpecificExitCode: 0,
        dwCheckPoint: 0,
        dwWaitHint: if current == SERVICE_START_PENDING {
            5000
        } else {
            0
        },
    };
    unsafe {
        SetServiceStatus(handle, &value);
    }
}
unsafe extern "system" fn control(
    code: u32,
    _event: u32,
    _data: *mut core::ffi::c_void,
    _context: *mut core::ffi::c_void,
) -> u32 {
    if code == SERVICE_CONTROL_STOP || code == SERVICE_CONTROL_SHUTDOWN {
        STOP.store(true, Ordering::Relaxed);
        state(SERVICE_STOP_PENDING);
    }
    0
}
unsafe extern "system" fn service_main(_argc: u32, _argv: *mut *mut u16) {
    let name = crate::wide(NAME);
    let handle =
        unsafe { RegisterServiceCtrlHandlerExW(name.as_ptr(), Some(control), ptr::null_mut()) };
    if handle.is_null() {
        return;
    }
    STATUS.store(handle as usize, Ordering::Relaxed);
    state(SERVICE_START_PENDING);
    state(SERVICE_RUNNING);
    let mut reader: Option<Reader> = None;
    let mut retry = None;
    while !STOP.load(Ordering::Relaxed) {
        if reader.is_none()
            && retry.is_none_or(|at: std::time::Instant| at.elapsed() >= Duration::from_secs(30))
        {
            retry = Some(std::time::Instant::now());
            match Reader::new() {
                Ok(r) => reader = Some(r),
                Err(e) => {
                    let _ = snapshot(None, e.to_string());
                }
            }
        }
        if let Some(r) = &reader {
            match r.read() {
                Ok(v) => {
                    let _ = snapshot(Some(v), String::new());
                }
                Err(e) => {
                    let _ = snapshot(None, e.to_string());
                    reader = None;
                }
            }
        }
        for _ in 0..40 {
            if STOP.load(Ordering::Relaxed) {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    let _ = snapshot(None, "CPU temperature service stopped".into());
    state(SERVICE_STOPPED);
}
fn serve() -> Result<()> {
    STOP.store(false, Ordering::Relaxed);
    let mut name = crate::wide(NAME);
    let table = [
        SERVICE_TABLE_ENTRYW {
            lpServiceName: name.as_mut_ptr(),
            lpServiceProc: Some(service_main),
        },
        SERVICE_TABLE_ENTRYW {
            lpServiceName: ptr::null_mut(),
            lpServiceProc: None,
        },
    ];
    ensure!(
        unsafe { StartServiceCtrlDispatcherW(table.as_ptr()) } != 0,
        "Cannot start temperature service dispatcher"
    );
    Ok(())
}
fn system_tool(name: &str) -> Result<PathBuf> {
    Ok(cpu_sensor::folder(&FOLDERID_System)?.join(name))
}
fn run_tool(name: &str, args: &[&std::ffi::OsStr]) -> Result<()> {
    let status = crate::processes::hidden(system_tool(name)?)
        .args(args)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()?;
    ensure!(status.success(), "{name} failed: {status}");
    Ok(())
}
fn protect(path: &Path) -> Result<()> {
    cpu_sensor::no_links(path)?;
    run_tool("icacls.exe", &[path.as_os_str(), "/reset".as_ref()])?;
    run_tool(
        "icacls.exe",
        &[path.as_os_str(), "/setowner".as_ref(), "*S-1-5-18".as_ref()],
    )?;
    run_tool(
        "icacls.exe",
        &[
            path.as_os_str(),
            "/inheritance:r".as_ref(),
            "/grant:r".as_ref(),
            "*S-1-5-18:(OI)(CI)F".as_ref(),
            "*S-1-5-32-544:(OI)(CI)F".as_ref(),
            "*S-1-5-32-545:(OI)(CI)RX".as_ref(),
        ],
    )
}
fn stop_existing() -> Result<bool> {
    unsafe {
        let manager = OpenSCManagerW(ptr::null(), ptr::null(), SC_MANAGER_CONNECT);
        ensure!(!manager.is_null(), "Administrator permission is required");
        let service = OpenServiceW(
            manager,
            crate::wide(NAME).as_ptr(),
            SERVICE_STOP | SERVICE_QUERY_STATUS,
        );
        if service.is_null() {
            let error = GetLastError();
            CloseServiceHandle(manager);
            ensure!(
                error == ERROR_SERVICE_DOES_NOT_EXIST,
                "Cannot access temperature service"
            );
            return Ok(false);
        }
        let result = (|| -> Result<bool> {
            let mut process_status: SERVICE_STATUS_PROCESS = std::mem::zeroed();
            let mut returned = 0;
            ensure!(
                QueryServiceStatusEx(
                    service,
                    SC_STATUS_PROCESS_INFO,
                    (&mut process_status as *mut SERVICE_STATUS_PROCESS).cast(),
                    std::mem::size_of::<SERVICE_STATUS_PROCESS>() as u32,
                    &mut returned
                ) != 0,
                "Cannot query service process"
            );
            let process = if process_status.dwProcessId > 0 {
                let handle = windows_sys::Win32::System::Threading::OpenProcess(
                    0x00100000,
                    0,
                    process_status.dwProcessId,
                );
                ensure!(!handle.is_null(), "Cannot wait for service process exit");
                Some(super::Handle(handle))
            } else {
                None
            };
            let mut status = std::mem::zeroed();
            ensure!(
                QueryServiceStatus(service, &mut status) != 0,
                "Cannot query temperature service"
            );
            if status.dwCurrentState == SERVICE_STOPPED {
                return Ok(false);
            }
            if status.dwCurrentState != SERVICE_STOP_PENDING {
                ensure!(
                    ControlService(service, SERVICE_CONTROL_STOP, &mut status) != 0,
                    "Cannot stop temperature service"
                );
            }
            for _ in 0..100 {
                std::thread::sleep(Duration::from_millis(100));
                ensure!(
                    QueryServiceStatus(service, &mut status) != 0,
                    "Cannot query stopped service"
                );
                if status.dwCurrentState == SERVICE_STOPPED {
                    if let Some(handle) = &process {
                        ensure!(
                            windows_sys::Win32::System::Threading::WaitForSingleObject(
                                handle.0, 5000
                            ) == WAIT_OBJECT_0,
                            "Service process has not exited"
                        );
                    }
                    return Ok(true);
                }
            }
            anyhow::bail!("Temperature service stop timed out")
        })();
        CloseServiceHandle(service);
        CloseServiceHandle(manager);
        result
    }
}
fn start_existing() -> Result<()> {
    unsafe {
        let manager = OpenSCManagerW(ptr::null(), ptr::null(), SC_MANAGER_CONNECT);
        ensure!(
            !manager.is_null(),
            "Cannot access temperature service manager"
        );
        let service = OpenServiceW(manager, crate::wide(NAME).as_ptr(), SERVICE_START);
        if service.is_null() {
            CloseServiceHandle(manager);
            anyhow::bail!("Cannot reopen previous temperature service");
        }
        let started = StartServiceW(service, 0, ptr::null()) != 0
            || GetLastError() == ERROR_SERVICE_ALREADY_RUNNING;
        CloseServiceHandle(service);
        CloseServiceHandle(manager);
        ensure!(started, "Cannot restart previous temperature service");
        Ok(())
    }
}
fn with_update_recovery<T>(
    update: impl FnOnce() -> Result<T>,
    recover: impl FnOnce() -> Result<()>,
) -> Result<T> {
    match update() {
        Ok(value) => Ok(value),
        Err(error) => match recover() {
            Ok(()) => Err(error),
            Err(recovery) => Err(error.context(format!(
                "Previous temperature service recovery failed: {recovery:#}"
            ))),
        },
    }
}
fn install() -> Result<()> {
    ensure!(
        cpu_sensor::supported(),
        "This CPU does not expose a supported temperature sensor"
    );
    let source = std::env::current_exe()?;
    let root = installation()?;
    cpu_sensor::no_links(&root)?;
    std::fs::create_dir_all(&root)?;
    protect(&root)?;
    let data = directory()?;
    cpu_sensor::no_links(&data)?;
    std::fs::create_dir_all(&data)?;
    protect(&data)?;
    let dll = cpu_sensor::folder(&FOLDERID_ProgramFiles)?.join("PawnIO/PawnIOLib.dll");
    if !dll.exists() {
        cpu_sensor::verify_installer()?;
        let installer = root.join("PawnIO_setup.exe");
        cpu_sensor::no_links(&installer)?;
        atomic_file(&installer, cpu_sensor::INSTALLER, true)?;
        cpu_sensor::verify_signature(&installer)?;
        let mut child = crate::processes::hidden(&installer)
            .args(["-install", "-silent"])
            .spawn()?;
        let start = std::time::Instant::now();
        let status = loop {
            if let Some(status) = child.try_wait()? {
                break status;
            }
            if start.elapsed() > Duration::from_secs(120) {
                let _ = child.kill();
                let _ = child.wait();
                anyhow::bail!("PawnIO installation timed out");
            }
            std::thread::sleep(Duration::from_millis(100));
        };
        ensure!(
            status.success() || status.code() == Some(3010),
            "Official PawnIO installation failed"
        );
    }
    cpu_sensor::verify_signature(&dll)?;
    // Verify a real sensor reading before registering an automatic service.
    let value = Reader::new()?.read()?;
    let target = root.join("CPlusCleanerTemperature.exe");
    cpu_sensor::no_links(&target)?;
    let bytes = (source != target)
        .then(|| std::fs::read(&source))
        .transpose()?;
    let previous = target
        .exists()
        .then(|| std::fs::read(&target))
        .transpose()?;
    let was_running = stop_existing()?;
    with_update_recovery(
        || {
            if let Some(bytes) = &bytes {
                replace_executable(&target, bytes)?;
            }
            let command = format!("\"{}\" --cpu-temperature-service", target.display());
            unsafe {
                let manager = OpenSCManagerW(
                    ptr::null(),
                    ptr::null(),
                    SC_MANAGER_CREATE_SERVICE | SC_MANAGER_CONNECT,
                );
                ensure!(!manager.is_null(), "Administrator permission is required");
                let mut service = OpenServiceW(
                    manager,
                    crate::wide(NAME).as_ptr(),
                    SERVICE_CHANGE_CONFIG | SERVICE_START,
                );
                if service.is_null() {
                    service = CreateServiceW(
                        manager,
                        crate::wide(NAME).as_ptr(),
                        crate::wide("CPlus Cleaner CPU Temperature").as_ptr(),
                        SERVICE_START | SERVICE_CHANGE_CONFIG,
                        SERVICE_WIN32_OWN_PROCESS,
                        SERVICE_AUTO_START,
                        SERVICE_ERROR_NORMAL,
                        crate::wide(&command).as_ptr(),
                        ptr::null(),
                        ptr::null_mut(),
                        ptr::null(),
                        ptr::null(),
                        ptr::null(),
                    );
                }
                if service.is_null() {
                    CloseServiceHandle(manager);
                    anyhow::bail!("Cannot register CPU temperature service");
                }
                let changed = ChangeServiceConfigW(
                    service,
                    SERVICE_NO_CHANGE,
                    SERVICE_AUTO_START,
                    SERVICE_NO_CHANGE,
                    crate::wide(&command).as_ptr(),
                    ptr::null(),
                    ptr::null_mut(),
                    ptr::null(),
                    ptr::null(),
                    ptr::null(),
                    ptr::null(),
                );
                let started = changed != 0 && StartServiceW(service, 0, ptr::null()) != 0;
                CloseServiceHandle(service);
                CloseServiceHandle(manager);
                ensure!(started, "Cannot start CPU temperature service");
            }
            snapshot(Some(value), String::new())?;
            Ok(())
        },
        || {
            if let Some(previous) = &previous {
                stop_existing()?;
                replace_executable(&target, previous)?;
                if was_running {
                    start_existing()?;
                }
            }
            Ok(())
        },
    )
}
fn replace_executable(target: &Path, bytes: &[u8]) -> Result<()> {
    // A failed replacement may leave the original intact but locked against renaming.
    if std::fs::read(target).ok().as_deref() == Some(bytes) {
        return Ok(());
    }
    let backup = target.with_file_name("CPlusCleanerTemperature.previous.exe");
    cpu_sensor::no_links(&backup)?;
    if backup.exists() {
        retry_file(|| std::fs::remove_file(&backup))?;
    }
    let existed = target.exists();
    if existed {
        retry_file(|| std::fs::rename(target, &backup))
            .context("Cannot preserve previous service executable")?;
    }
    if let Err(error) = atomic_file(target, bytes, true) {
        if existed {
            retry_file(|| std::fs::rename(&backup, target))
                .context("Cannot restore previous service executable")?;
        }
        return Err(error);
    }
    if existed {
        let _ = std::fs::remove_file(backup);
    }
    Ok(())
}
fn uninstall() -> Result<()> {
    stop_existing()?;
    unsafe {
        let manager = OpenSCManagerW(ptr::null(), ptr::null(), SC_MANAGER_CONNECT);
        ensure!(!manager.is_null(), "Administrator permission is required");
        let service = OpenServiceW(manager, crate::wide(NAME).as_ptr(), 0x00010000);
        if !service.is_null() {
            let ok = DeleteService(service);
            CloseServiceHandle(service);
            CloseServiceHandle(manager);
            ensure!(ok != 0, "Cannot remove temperature service");
        } else {
            let error = GetLastError();
            CloseServiceHandle(manager);
            ensure!(
                error == ERROR_SERVICE_DOES_NOT_EXIST,
                "Cannot access temperature service"
            );
        }
    }
    // PawnIO may be shared with other applications and is never removed here.
    for path in [
        installation()?.join("CPlusCleanerTemperature.exe"),
        installation()?.join("CPlusCleanerTemperature.previous.exe"),
        installation()?.join("PawnIO_setup.exe"),
        directory()?.join("temperature.json"),
    ] {
        cpu_sensor::no_links(&path)?;
        if path.exists() {
            retry_file(|| std::fs::remove_file(&path))?;
        }
    }
    Ok(())
}
pub fn command() -> Result<bool> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 1 {
        return Ok(false);
    }
    match args[0].as_str() {
        "--cpu-temperature-service" => serve()?,
        "--install-cpu-temperature" => {
            if let Err(e) = install() {
                let _ = snapshot(None, e.to_string());
                return Err(e);
            }
        }
        "--uninstall-cpu-temperature" => uninstall()?,
        _ => return Ok(false),
    }
    Ok(true)
}
pub fn enable() -> Result<crate::shell::ElevatedProcess> {
    crate::shell::elevate(&std::env::current_exe()?, "--install-cpu-temperature")
        .context("CPU temperature setup was cancelled or could not be started")
}
pub fn failure() -> String {
    directory()
        .ok()
        .and_then(|p| bounded_snapshot(&p.join("temperature.json")))
        .and_then(|b| serde_json::from_slice::<Snapshot>(&b).ok())
        .map(|s| s.error)
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "CPU temperature setup failed".into())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unchanged_executable_can_be_recovered_while_locked_against_replacement() {
        use std::os::windows::fs::OpenOptionsExt;
        let root =
            std::env::temp_dir().join(format!("cplus-cpu-locked-{}-{}", std::process::id(), now()));
        std::fs::create_dir(&root).unwrap();
        let target = root.join("service.exe");
        std::fs::write(&target, b"previous executable").unwrap();
        let lock = std::fs::OpenOptions::new()
            .read(true)
            .share_mode(windows_sys::Win32::Storage::FileSystem::FILE_SHARE_READ)
            .open(&target)
            .unwrap();
        replace_executable(&target, b"previous executable").unwrap();
        assert_eq!(std::fs::read(&target).unwrap(), b"previous executable");
        drop(lock);
        std::fs::remove_file(target).unwrap();
        std::fs::remove_dir(root).unwrap();
    }
    #[test]
    fn failed_update_invokes_recovery_and_preserves_original_error() {
        use std::cell::Cell;
        let recoveries = Cell::new(0);
        let result: Result<()> = with_update_recovery(
            || anyhow::bail!("injected activation failure"),
            || {
                recoveries.set(recoveries.get() + 1);
                Ok(())
            },
        );
        assert_eq!(recoveries.get(), 1);
        assert_eq!(
            result.unwrap_err().to_string(),
            "injected activation failure"
        );
    }
    #[test]
    fn successful_update_skips_recovery_and_recovery_errors_are_reported() {
        let result =
            with_update_recovery(|| Ok(7), || panic!("successful update must not roll back"));
        assert_eq!(result.unwrap(), 7);
        let result = with_update_recovery::<()>(
            || anyhow::bail!("update failed"),
            || anyhow::bail!("restart failed"),
        );
        let error = format!("{:#}", result.unwrap_err());
        assert!(error.contains("update failed") && error.contains("restart failed"));
    }
    #[test]
    fn transient_file_busy_is_retried_but_other_errors_are_returned() {
        let mut calls = 0;
        let value = retry_file(|| {
            calls += 1;
            if calls < 3 {
                Err(std::io::Error::from_raw_os_error(32))
            } else {
                Ok(7)
            }
        })
        .unwrap();
        assert_eq!((value, calls), (7, 3));
        let mut calls = 0;
        let error = retry_file::<()>(|| {
            calls += 1;
            Err(std::io::Error::from_raw_os_error(2))
        })
        .unwrap_err();
        assert_eq!(calls, 1);
        assert_eq!(error.raw_os_error(), Some(2));
    }
    #[test]
    fn snapshot_replacement_cannot_overwrite_a_hardlinked_target() {
        let root =
            std::env::temp_dir().join(format!("cplus-cpu-atomic-{}-{}", std::process::id(), now()));
        std::fs::create_dir(&root).unwrap();
        let victim = root.join("original");
        let dest = root.join("snapshot");
        std::fs::write(&victim, b"original").unwrap();
        std::fs::hard_link(&victim, &dest).unwrap();
        atomic_file(&dest, b"replacement", false).unwrap();
        assert_eq!(std::fs::read(&victim).unwrap(), b"original");
        assert_eq!(std::fs::read(&dest).unwrap(), b"replacement");
        std::fs::remove_file(victim).unwrap();
        std::fs::remove_file(dest).unwrap();
        std::fs::remove_dir(root).unwrap();
    }
    #[test]
    fn expired_or_invalid_snapshots_cannot_supply_cpu_temperature() {
        let mut s = Snapshot {
            schema: 1,
            sampled_ms: 10000,
            celsius: Some(54.0),
            error: String::new(),
        };
        assert_eq!(validate(&s, 11000), Some(54.0));
        assert_eq!(validate(&s, 30000), None);
        s.celsius = Some(f64::NAN);
        assert_eq!(validate(&s, 11000), None);
        s.celsius = Some(54.0);
        assert_eq!(validate(&s, 1000), None);
    }
}
