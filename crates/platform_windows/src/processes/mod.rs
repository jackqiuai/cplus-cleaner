use crate::{from_wide, wide};
use serde::{Deserialize, Serialize};
use std::{
    io, mem,
    os::windows::process::CommandExt,
    path::Path,
    process::{Command, Stdio},
};
use windows_sys::Win32::{
    Foundation::*,
    System::{Diagnostics::ToolHelp::*, Threading::*},
};

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Process {
    pub pid: u32,
    pub created: u64,
    pub path: String,
    pub name: String,
}
pub fn describe(pid: u32) -> Option<Process> {
    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        return None;
    }
    let result = describe_handle(handle, pid);
    unsafe {
        CloseHandle(handle);
    }
    result
}
fn describe_handle(handle: HANDLE, pid: u32) -> Option<Process> {
    let mut path = vec![0u16; 32768];
    let mut size = path.len() as u32;
    let mut created: FILETIME = unsafe { mem::zeroed() };
    let mut exited = created;
    let mut kernel = created;
    let mut user = created;
    if unsafe { QueryFullProcessImageNameW(handle, 0, path.as_mut_ptr(), &mut size) } == 0 {
        return None;
    }
    if unsafe { GetProcessTimes(handle, &mut created, &mut exited, &mut kernel, &mut user) } == 0 {
        return None;
    }
    let path = String::from_utf16_lossy(&path[..size as usize]);
    Some(Process {
        pid,
        created: ((created.dwHighDateTime as u64) << 32) | created.dwLowDateTime as u64,
        name: Path::new(&path).file_name()?.to_string_lossy().into_owned(),
        path,
    })
}
pub fn list() -> io::Result<Vec<Process>> {
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
    if snapshot == INVALID_HANDLE_VALUE {
        return Err(io::Error::last_os_error());
    }
    let mut entry: PROCESSENTRY32W = unsafe { mem::zeroed() };
    entry.dwSize = mem::size_of_val(&entry) as u32;
    let mut result = vec![];
    let mut ok = unsafe { Process32FirstW(snapshot, &mut entry) };
    while ok != 0 {
        if let Some(mut p) = describe(entry.th32ProcessID) {
            p.name = from_wide(&entry.szExeFile);
            result.push(p);
        }
        ok = unsafe { Process32NextW(snapshot, &mut entry) };
    }
    unsafe {
        CloseHandle(snapshot);
    }
    Ok(result)
}
pub fn hidden(executable: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut command = Command::new(executable);
    command.creation_flags(CREATE_NO_WINDOW);
    command
}
pub fn run(executable: &Path, args: &[String]) -> io::Result<std::process::Output> {
    hidden(executable).args(args).stdin(Stdio::null()).output()
}
pub fn io_read_bytes(pid: u32) -> Option<u64> {
    let handle = unsafe { OpenProcess(PROCESS_QUERY_INFORMATION, 0, pid) };
    if handle.is_null() {
        return None;
    }
    let mut counters: IO_COUNTERS = unsafe { mem::zeroed() };
    let ok = unsafe { GetProcessIoCounters(handle, &mut counters) };
    unsafe {
        CloseHandle(handle);
    }
    if ok == 0 {
        None
    } else {
        Some(counters.ReadTransferCount)
    }
}

/// Only the explicitly selected popup kill action may call this function.
pub fn terminate_snapshot(expected: &Process) -> io::Result<()> {
    let handle = unsafe {
        OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE,
            0,
            expected.pid,
        )
    };
    if handle.is_null() {
        return Err(io::Error::last_os_error());
    }
    let same = describe_handle(handle, expected.pid).is_some_and(|p| {
        p.created == expected.created && p.path.eq_ignore_ascii_case(&expected.path)
    });
    if !same {
        unsafe {
            CloseHandle(handle);
        }
        return Err(io::Error::other("Process identity changed"));
    }
    let ok = unsafe { TerminateProcess(handle, 1) };
    let error = io::Error::last_os_error();
    unsafe {
        CloseHandle(handle);
    }
    if ok == 0 { Err(error) } else { Ok(()) }
}

pub struct SingleInstance {
    mutex: HANDLE,
    event: HANDLE,
    pub primary: bool,
}
impl SingleInstance {
    pub fn acquire(name: &str, activate: bool) -> io::Result<Self> {
        let event = unsafe {
            CreateEventW(
                std::ptr::null(),
                0,
                0,
                wide(format!("{name}.activate")).as_ptr(),
            )
        };
        if event.is_null() {
            return Err(io::Error::last_os_error());
        }
        let mutex = unsafe { CreateMutexW(std::ptr::null(), 0, wide(name).as_ptr()) };
        let existed = unsafe { GetLastError() } == ERROR_ALREADY_EXISTS;
        if mutex.is_null() {
            let error = io::Error::last_os_error();
            unsafe {
                CloseHandle(event);
            }
            return Err(error);
        }
        if existed && activate {
            unsafe {
                SetEvent(event);
            }
        }
        Ok(Self {
            mutex,
            event,
            primary: !existed,
        })
    }
    pub fn activation_pending(&self) -> bool {
        self.primary && unsafe { WaitForSingleObject(self.event, 0) } == WAIT_OBJECT_0
    }
}
impl Drop for SingleInstance {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.event);
            CloseHandle(self.mutex);
        }
    }
}
pub fn activation_requested(name: &str) -> bool {
    let handle = unsafe { OpenEventW(0x00100000, 0, wide(format!("{name}.activate")).as_ptr()) };
    if handle.is_null() {
        return false;
    }
    let requested = unsafe { WaitForSingleObject(handle, 0) } == WAIT_OBJECT_0;
    unsafe {
        CloseHandle(handle);
    }
    requested
}
