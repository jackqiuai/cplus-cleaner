//! Fixed read-only temperature operations using the official signed PawnIO modules.
use anyhow::{Context, Result, bail, ensure};
use sha2::{Digest, Sha256};
use std::{
    ffi::CStr,
    mem,
    path::{Path, PathBuf},
    ptr,
};
use windows_sys::Win32::{
    Foundation::*,
    Security::WinTrust::*,
    System::{LibraryLoader::*, Threading::*},
    UI::Shell::*,
};

pub const INSTALLER: &[u8] = include_bytes!("../../vendors/pawnio/PawnIO_setup.exe");
pub const NOTICE: &str = include_str!("../../vendors/pawnio/NOTICE.md");
pub const LICENSE: &str = include_str!("../../vendors/pawnio/COPYING");
const INTEL: &[u8] = include_bytes!("../../vendors/pawnio/IntelMSR.bin");
const AMD: &[u8] = include_bytes!("../../vendors/pawnio/AMDFamily17.bin");
pub fn folder(id: &windows_sys::core::GUID) -> Result<PathBuf> {
    let mut value = ptr::null_mut();
    ensure!(
        unsafe { SHGetKnownFolderPath(id, 0, ptr::null_mut(), &mut value) } >= 0,
        "Windows folder unavailable"
    );
    let mut len = 0;
    unsafe {
        while *value.add(len) != 0 {
            len += 1;
        }
    }
    let path = PathBuf::from(crate::from_wide(unsafe {
        std::slice::from_raw_parts(value, len)
    }));
    unsafe {
        windows_sys::Win32::System::Com::CoTaskMemFree(value.cast());
    }
    Ok(path)
}
pub fn no_links(path: &Path) -> Result<()> {
    use std::os::windows::fs::MetadataExt;
    for parent in path.ancestors() {
        if let Ok(meta) = std::fs::symlink_metadata(parent) {
            ensure!(
                meta.file_attributes() & 0x400 == 0,
                "Linked component rejected"
            );
        }
    }
    Ok(())
}
pub fn verify_signature(path: &Path) -> Result<()> {
    no_links(path)?;
    let wide = crate::wide(path);
    let mut file = WINTRUST_FILE_INFO {
        cbStruct: mem::size_of::<WINTRUST_FILE_INFO>() as u32,
        pcwszFilePath: wide.as_ptr(),
        ..unsafe { mem::zeroed() }
    };
    let mut data = WINTRUST_DATA {
        cbStruct: mem::size_of::<WINTRUST_DATA>() as u32,
        dwUIChoice: WTD_UI_NONE,
        fdwRevocationChecks: WTD_REVOKE_NONE,
        dwUnionChoice: WTD_CHOICE_FILE,
        Anonymous: WINTRUST_DATA_0 { pFile: &mut file },
        dwStateAction: WTD_STATEACTION_VERIFY,
        dwProvFlags: WTD_CACHE_ONLY_URL_RETRIEVAL,
        ..unsafe { mem::zeroed() }
    };
    let mut action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
    let status = unsafe {
        WinVerifyTrust(
            INVALID_HANDLE_VALUE,
            &mut action,
            (&mut data as *mut WINTRUST_DATA).cast(),
        )
    };
    data.dwStateAction = WTD_STATEACTION_CLOSE;
    unsafe {
        WinVerifyTrust(
            INVALID_HANDLE_VALUE,
            &mut action,
            (&mut data as *mut WINTRUST_DATA).cast(),
        );
    }
    ensure!(
        status == 0,
        "PawnIO signature verification failed: {status:#x}"
    );
    Ok(())
}
fn pinned(bytes: &[u8], expected: &str) -> Result<()> {
    ensure!(
        format!("{:x}", Sha256::digest(bytes)) == expected,
        "PawnIO component digest mismatch"
    );
    Ok(())
}
pub fn verify_installer() -> Result<()> {
    pinned(
        INSTALLER,
        "1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032",
    )
}
#[derive(Clone, Copy, Debug)]
enum Sensor {
    Intel,
    Amd(u8),
}
fn sensor() -> Option<Sensor> {
    #[cfg(target_arch = "x86_64")]
    {
        use std::arch::x86_64::__cpuid;
        let v = __cpuid(0);
        let info = __cpuid(1);
        let family = (info.eax >> 8) & 15;
        if [v.ebx, v.edx, v.ecx] == [0x756e6547, 0x49656e69, 0x6c65746e]
            && family == 6
            && __cpuid(6).eax & 0x41 == 0x41
        {
            return Some(Sensor::Intel);
        }
        let family = family
            + if family == 15 {
                (info.eax >> 20) & 255
            } else {
                0
            };
        if [v.ebx, v.edx, v.ecx] == [0x68747541, 0x69746e65, 0x444d4163]
            && matches!(family, 0x17 | 0x19 | 0x1a)
        {
            let mut brand = Vec::new();
            for leaf in 0x80000002..=0x80000004 {
                let r = __cpuid(leaf);
                for w in [r.eax, r.ebx, r.ecx, r.edx] {
                    brand.extend(w.to_le_bytes());
                }
            }
            let name = String::from_utf8_lossy(&brand);
            let name = name.trim_matches(['\0', ' ']);
            let offset = if family != 0x17 {
                0
            } else if [
                "AMD Ryzen 5 1600X",
                "AMD Ryzen 7 1700X",
                "AMD Ryzen 7 1800X",
            ]
            .iter()
            .any(|s| name.starts_with(s))
            {
                20
            } else if name.starts_with("AMD Ryzen 7 2700X") {
                10
            } else if name.starts_with("AMD Ryzen Threadripper 19")
                || name.starts_with("AMD Ryzen Threadripper 29")
            {
                27
            } else {
                0
            };
            return Some(Sensor::Amd(offset));
        }
    }
    None
}
pub fn supported() -> bool {
    static SUPPORTED: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
    *SUPPORTED.get_or_init(|| sensor().is_some() && single_package())
}
fn single_package() -> bool {
    use windows_sys::Win32::System::SystemInformation::*;
    let mut bytes = 0;
    unsafe {
        GetLogicalProcessorInformationEx(RelationProcessorPackage, ptr::null_mut(), &mut bytes);
    }
    if !(8..=1048576).contains(&bytes) {
        return false;
    }
    let mut buffer = vec![0u64; (bytes as usize).div_ceil(8)];
    if unsafe {
        GetLogicalProcessorInformationEx(
            RelationProcessorPackage,
            buffer.as_mut_ptr().cast(),
            &mut bytes,
        )
    } == 0
    {
        return false;
    }
    let data = unsafe { std::slice::from_raw_parts(buffer.as_ptr().cast::<u8>(), bytes as usize) };
    let mut count = 0;
    let mut offset = 0;
    while offset + 8 <= data.len() {
        let size = u32::from_le_bytes(data[offset + 4..offset + 8].try_into().unwrap()) as usize;
        if size < 8 || offset + size > data.len() {
            return false;
        }
        count += 1;
        offset += size;
    }
    count == 1 && offset == data.len()
}
type Open = unsafe extern "system" fn(*mut HANDLE) -> i32;
type Load = unsafe extern "system" fn(HANDLE, *const u8, usize) -> i32;
type Execute = unsafe extern "system" fn(
    HANDLE,
    *const u8,
    *const u64,
    usize,
    *mut u64,
    usize,
    *mut usize,
) -> i32;
type Close = unsafe extern "system" fn(HANDLE) -> i32;
pub struct Reader {
    library: HMODULE,
    handle: HANDLE,
    execute: Execute,
    close: Close,
    sensor: Sensor,
}
impl Reader {
    pub fn new() -> Result<Self> {
        ensure!(supported(), "CPU package layout is not supported");
        let sensor = sensor().context("Unsupported CPU temperature sensor")?;
        let (module, hash) = match sensor {
            Sensor::Intel => (
                INTEL,
                "d6ed85d65ab17a22f813ef98207d6d537155ee2ded5976a21cb48413c9b92e5f",
            ),
            Sensor::Amd(_) => (
                AMD,
                "dae74615761b78bdf064dfb3e136252ddcc6fc727d88f14738d0e5800d427a91",
            ),
        };
        pinned(module, hash)?;
        let path = folder(&FOLDERID_ProgramFiles)?.join("PawnIO/PawnIOLib.dll");
        verify_signature(&path)?;
        let library = unsafe {
            LoadLibraryExW(
                crate::wide(path).as_ptr(),
                ptr::null_mut(),
                LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32,
            )
        };
        ensure!(!library.is_null(), "Cannot load official PawnIOLib");
        let resolve = |name: &CStr| {
            unsafe { GetProcAddress(library, name.as_ptr().cast()) }
                .context("Missing PawnIO export")
        };
        let created = (|| -> Result<Self> {
            let open: Open = unsafe { mem::transmute(resolve(c"pawnio_open")?) };
            let load: Load = unsafe { mem::transmute(resolve(c"pawnio_load")?) };
            let execute: Execute = unsafe { mem::transmute(resolve(c"pawnio_execute")?) };
            let close: Close = unsafe { mem::transmute(resolve(c"pawnio_close")?) };
            let mut handle = ptr::null_mut();
            ensure!(
                unsafe { open(&mut handle) } >= 0,
                "PawnIO access denied; enable the CPU temperature service"
            );
            if unsafe { load(handle, module.as_ptr(), module.len()) } < 0 {
                unsafe {
                    close(handle);
                };
                bail!("PawnIO rejected signed CPU module");
            }
            Ok(Self {
                library,
                handle,
                execute,
                close,
                sensor,
            })
        })();
        if created.is_err() {
            unsafe {
                FreeLibrary(library);
            }
        }
        created
    }
    fn execute(&self, name: &CStr, input: u64) -> Result<u64> {
        let mut out = 0;
        let mut size = 0;
        ensure!(
            unsafe {
                (self.execute)(
                    self.handle,
                    name.as_ptr().cast(),
                    &input,
                    1,
                    &mut out,
                    1,
                    &mut size,
                )
            } >= 0
                && size == 1,
            "CPU temperature read unavailable"
        );
        Ok(out)
    }
    pub fn read(&self) -> Result<f64> {
        match self.sensor {
            Sensor::Intel => {
                let _affinity = CpuAffinity::first()?;
                let target = self.execute(c"ioctl_read_msr", 0x1a2)?;
                let status = self.execute(c"ioctl_read_msr", 0x1b1)?;
                intel(target, status).context("CPU returned an invalid thermal reading")
            }
            Sensor::Amd(offset) => {
                // SMN index/data access must coordinate with other monitoring applications.
                let mut descriptor = ptr::null_mut();
                ensure!(
                    unsafe {
                        windows_sys::Win32::Security::Authorization::ConvertStringSecurityDescriptorToSecurityDescriptorW(crate::wide("D:(A;;GA;;;WD)").as_ptr(),1,&mut descriptor,ptr::null_mut())
                    } != 0,
                    "PCI mutex security unavailable"
                );
                let attributes = windows_sys::Win32::Security::SECURITY_ATTRIBUTES {
                    nLength: mem::size_of::<windows_sys::Win32::Security::SECURITY_ATTRIBUTES>()
                        as u32,
                    lpSecurityDescriptor: descriptor,
                    bInheritHandle: 0,
                };
                let mutex = unsafe {
                    CreateMutexW(&attributes, 0, crate::wide("Global\\Access_PCI").as_ptr())
                };
                unsafe {
                    LocalFree(descriptor);
                }
                ensure!(!mutex.is_null(), "PCI mutex unavailable");
                let wait = unsafe { WaitForSingleObject(mutex, 100) };
                let result = if wait == WAIT_OBJECT_0 || wait == WAIT_ABANDONED {
                    let r = self.execute(c"ioctl_read_smn", 0x59800);
                    unsafe {
                        ReleaseMutex(mutex);
                    };
                    r.and_then(|r| amd(r, offset).context("Invalid AMD temperature"))
                } else {
                    Err(anyhow::anyhow!("PCI temperature busy"))
                };
                unsafe {
                    CloseHandle(mutex);
                }
                result
            }
        }
    }
}
struct CpuAffinity(windows_sys::Win32::System::SystemInformation::GROUP_AFFINITY);
impl CpuAffinity {
    fn first() -> Result<Self> {
        let next = windows_sys::Win32::System::SystemInformation::GROUP_AFFINITY {
            Mask: 1,
            Group: 0,
            Reserved: [0; 3],
        };
        let mut old = unsafe { mem::zeroed() };
        ensure!(
            unsafe { SetThreadGroupAffinity(GetCurrentThread(), &next, &mut old) } != 0,
            "Cannot select a consistent CPU temperature source"
        );
        Ok(Self(old))
    }
}
impl Drop for CpuAffinity {
    fn drop(&mut self) {
        unsafe {
            SetThreadGroupAffinity(GetCurrentThread(), &self.0, ptr::null_mut());
        }
    }
}
impl Drop for Reader {
    fn drop(&mut self) {
        unsafe {
            (self.close)(self.handle);
            FreeLibrary(self.library);
        }
    }
}
fn intel(target: u64, status: u64) -> Option<f64> {
    let target = (target >> 16) & 255;
    if !(50..=125).contains(&target) || status & (1 << 31) == 0 {
        return None;
    }
    let value = target as f64 - ((status >> 16) & 127) as f64;
    valid(value)
}
fn amd(raw: u64, offset: u8) -> Option<f64> {
    let raw = u32::try_from(raw).ok()?;
    if raw == 0 || raw == u32::MAX {
        return None;
    }
    valid(
        (raw >> 21) as f64 * 0.125
            - if raw & (1 << 19) != 0 || raw & (3 << 16) == 3 << 16 {
                49.0
            } else {
                0.0
            }
            - offset as f64,
    )
}
fn valid(v: f64) -> Option<f64> {
    (v.is_finite() && v > 0.0 && v <= 125.0).then_some(v)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn existing_driver_probe_is_read_only() {
        let Ok(root) = folder(&FOLDERID_ProgramFiles) else {
            return;
        };
        if root.join("PawnIO/PawnIOLib.dll").exists() && supported() {
            let value = Reader::new().and_then(|r| r.read());
            eprintln!("Existing official PawnIO CPU temperature probe: {value:?}");
            if let Ok(value) = value {
                assert!(value > 0.0 && value <= 125.0);
            }
        }
    }
    #[test]
    fn temperatures_are_validated() {
        assert_eq!(intel(100 << 16, (1 << 31) | (42 << 16)), Some(58.0));
        assert_eq!(intel(100 << 16, 42 << 16), None);
        assert_eq!(amd(520 << 21, 0), Some(65.0));
        assert_eq!(amd((912 << 21) | (1 << 19), 0), Some(65.0));
        assert_eq!(amd(0, 0), None);
        assert!(
            pinned(
                INTEL,
                "d6ed85d65ab17a22f813ef98207d6d537155ee2ded5976a21cb48413c9b92e5f"
            )
            .is_ok()
        );
    }
}
