use crate::{from_wide, wide};
use std::{io, ptr};
pub use windows_sys::Win32::System::Registry::{KEY_WOW64_32KEY, KEY_WOW64_64KEY};
#[derive(Clone, Copy, Debug)]
pub enum Hive {
    CurrentUser,
    LocalMachine,
}
impl Hive {
    fn raw(self) -> HKEY {
        match self {
            Self::CurrentUser => windows_sys::Win32::System::Registry::HKEY_CURRENT_USER,
            Self::LocalMachine => windows_sys::Win32::System::Registry::HKEY_LOCAL_MACHINE,
        }
    }
}
pub const HKEY_CURRENT_USER: Hive = Hive::CurrentUser;
pub const HKEY_LOCAL_MACHINE: Hive = Hive::LocalMachine;
use windows_sys::Win32::{Foundation::*, System::Registry::*};

pub struct Key(HKEY);
impl Drop for Key {
    fn drop(&mut self) {
        unsafe {
            RegCloseKey(self.0);
        }
    }
}
impl Key {
    pub fn open(hive: Hive, path: &str, view: u32, write: bool) -> io::Result<Self> {
        let mut key = ptr::null_mut();
        let status = unsafe {
            RegOpenKeyExW(
                hive.raw(),
                wide(path).as_ptr(),
                0,
                KEY_READ
                    | view
                    | if write {
                        KEY_SET_VALUE | KEY_CREATE_SUB_KEY
                    } else {
                        0
                    },
                &mut key,
            )
        };
        if status != 0 {
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        Ok(Self(key))
    }
    pub fn create(hive: Hive, path: &str) -> io::Result<Self> {
        let mut key = ptr::null_mut();
        let status = unsafe {
            RegCreateKeyExW(
                hive.raw(),
                wide(path).as_ptr(),
                0,
                ptr::null(),
                0,
                KEY_READ | KEY_WRITE,
                ptr::null(),
                &mut key,
                ptr::null_mut(),
            )
        };
        if status != 0 {
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        Ok(Self(key))
    }
    pub fn raw(&self, name: &str) -> io::Result<(u32, Vec<u8>)> {
        let (mut kind, mut len) = (0, 0);
        let status = unsafe {
            RegQueryValueExW(
                self.0,
                wide(name).as_ptr(),
                ptr::null(),
                &mut kind,
                ptr::null_mut(),
                &mut len,
            )
        };
        if status != 0 {
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        if len > 16 * 1024 * 1024 {
            return Err(io::Error::other("Registry value exceeds supported size"));
        }
        let mut bytes = vec![0u8; len as usize];
        let status = unsafe {
            RegQueryValueExW(
                self.0,
                wide(name).as_ptr(),
                ptr::null(),
                &mut kind,
                bytes.as_mut_ptr(),
                &mut len,
            )
        };
        if status != 0 {
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        bytes.truncate(len as usize);
        Ok((kind, bytes))
    }
    pub fn string(&self, name: &str) -> String {
        self.raw(name)
            .ok()
            .filter(|(t, _)| *t == REG_SZ || *t == REG_EXPAND_SZ)
            .map(|(_, v)| {
                from_wide(
                    &v.as_chunks::<2>()
                        .0
                        .iter()
                        .map(|b| u16::from_le_bytes([b[0], b[1]]))
                        .collect::<Vec<_>>(),
                )
            })
            .unwrap_or_default()
    }
    pub fn number(&self, name: &str) -> u32 {
        self.raw(name)
            .ok()
            .and_then(|(_, v)| {
                v.get(..4)
                    .map(|b| u32::from_le_bytes(b.try_into().unwrap()))
            })
            .unwrap_or(0)
    }
    pub fn subkeys(&self) -> Vec<String> {
        let mut result = vec![];
        let mut index = 0;
        loop {
            let mut name = vec![0u16; 16384];
            let mut len = name.len() as u32;
            let status = unsafe {
                RegEnumKeyExW(
                    self.0,
                    index,
                    name.as_mut_ptr(),
                    &mut len,
                    ptr::null(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                )
            };
            if status == ERROR_NO_MORE_ITEMS {
                break;
            }
            if status != 0 {
                break;
            }
            result.push(String::from_utf16_lossy(&name[..len as usize]));
            index += 1;
        }
        result
    }
    pub fn values(&self) -> Vec<(String, u32, Vec<u8>)> {
        let mut result = vec![];
        let mut index = 0;
        loop {
            let mut name = vec![0u16; 16384];
            let mut name_len = name.len() as u32;
            let (mut kind, mut len) = (0, 0);
            let status = unsafe {
                RegEnumValueW(
                    self.0,
                    index,
                    name.as_mut_ptr(),
                    &mut name_len,
                    ptr::null(),
                    &mut kind,
                    ptr::null_mut(),
                    &mut len,
                )
            };
            if status == ERROR_NO_MORE_ITEMS {
                break;
            }
            if status != 0 && status != ERROR_MORE_DATA {
                break;
            }
            let name = String::from_utf16_lossy(&name[..name_len as usize]);
            if let Ok((kind, bytes)) = self.raw(&name) {
                result.push((name, kind, bytes));
            }
            index += 1;
        }
        result
    }
    pub fn set_string(&self, name: &str, value: &str) -> io::Result<()> {
        let value = wide(value);
        let status = unsafe {
            RegSetValueExW(
                self.0,
                wide(name).as_ptr(),
                0,
                REG_SZ,
                value.as_ptr().cast(),
                (value.len() * 2) as u32,
            )
        };
        if status != 0 {
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        Ok(())
    }
    pub fn delete_value(&self, name: &str) -> io::Result<()> {
        let status = unsafe { RegDeleteValueW(self.0, wide(name).as_ptr()) };
        if status != 0 {
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        Ok(())
    }
    pub fn set_raw(&self, name: &str, kind: u32, bytes: &[u8]) -> io::Result<()> {
        let status = unsafe {
            RegSetValueExW(
                self.0,
                wide(name).as_ptr(),
                0,
                kind,
                bytes.as_ptr(),
                bytes.len() as u32,
            )
        };
        if status != 0 {
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        Ok(())
    }
    pub fn delete_child(&self, name: &str) -> io::Result<()> {
        if name.is_empty() || name.contains(['\\', '/']) {
            return Err(io::Error::other("Invalid registry child"));
        }
        let status = unsafe { RegDeleteKeyW(self.0, wide(name).as_ptr()) };
        if status != 0 {
            return Err(io::Error::from_raw_os_error(status as i32));
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Default, serde::Serialize, serde::Deserialize)]
#[serde(default)]
pub struct InstalledRecord {
    pub key: String,
    pub hive: String,
    pub view: u32,
    pub name: String,
    pub version: String,
    pub publisher: String,
    pub location: String,
    pub uninstall: String,
    pub quiet_uninstall: String,
    pub icon: String,
    pub date: String,
    pub estimated_kib: u64,
    pub system: bool,
    pub parent: String,
    pub msi: bool,
    pub release_type: String,
}
pub fn installed() -> Vec<InstalledRecord> {
    let root = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall";
    let mut records = vec![];
    for (hive, hive_name) in [(HKEY_CURRENT_USER, "HKCU"), (HKEY_LOCAL_MACHINE, "HKLM")] {
        for view in [KEY_WOW64_32KEY, KEY_WOW64_64KEY] {
            let Ok(parent) = Key::open(hive, root, view, false) else {
                continue;
            };
            for name in parent.subkeys() {
                let Ok(key) = Key::open(hive, &format!("{root}\\{name}"), view, false) else {
                    continue;
                };
                let title = key.string("DisplayName");
                if title.trim().is_empty() {
                    continue;
                }
                records.push(InstalledRecord {
                    key: name,
                    hive: hive_name.into(),
                    view,
                    name: title,
                    version: key.string("DisplayVersion"),
                    publisher: key.string("Publisher"),
                    location: {
                        let location = key.string("InstallLocation");
                        if location.trim().is_empty() {
                            key.string("LocationRoot")
                        } else {
                            location
                        }
                    },
                    uninstall: key.string("UninstallString"),
                    quiet_uninstall: key.string("QuietUninstallString"),
                    icon: key.string("DisplayIcon"),
                    date: key.string("InstallDate"),
                    estimated_kib: key.number("EstimatedSize") as u64,
                    system: key.number("SystemComponent") != 0,
                    parent: key.string("ParentKeyName"),
                    msi: key.number("WindowsInstaller") == 1,
                    release_type: key.string("ReleaseType"),
                });
            }
        }
    }
    records
}

pub fn service_images() -> Vec<String> {
    let root = r"SYSTEM\CurrentControlSet\Services";
    let Ok(parent) = Key::open(HKEY_LOCAL_MACHINE, root, 0, false) else {
        return vec![];
    };
    parent
        .subkeys()
        .into_iter()
        .filter_map(|name| Key::open(HKEY_LOCAL_MACHINE, &format!("{root}\\{name}"), 0, false).ok())
        .map(|key| key.string("ImagePath"))
        .filter(|s| !s.is_empty())
        .collect()
}
pub fn msi_property(product: &str, name: &str) -> String {
    let mut bytes = [0u16; 32768];
    let mut len = bytes.len() as u32;
    let result = unsafe {
        windows_sys::Win32::System::ApplicationInstallationAndServicing::MsiGetProductInfoW(
            wide(product).as_ptr(),
            wide(name).as_ptr(),
            bytes.as_mut_ptr(),
            &mut len,
        )
    };
    if result == 0 {
        from_wide(&bytes)
    } else {
        String::new()
    }
}
