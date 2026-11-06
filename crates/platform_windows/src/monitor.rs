//! Read-only system samples. All counters are kept on one background thread.
pub mod taskbar;
use crate::{filesystem, from_wide, wide};
use std::{collections::HashMap, mem, path::PathBuf, ptr, time::Instant};
use windows_sys::Win32::{
    Foundation::*,
    NetworkManagement::{IpHelper::*, Ndis::IfOperStatusUp},
    Networking::WinSock::*,
    System::{
        Diagnostics::ToolHelp::*, Performance::*, ProcessStatus::*, SystemInformation::*,
        Threading::*,
    },
};

#[derive(Clone, Debug, Default)]
pub struct Process {
    pub pid: u32,
    pub name: String,
    pub path: PathBuf,
    pub cpu: Option<f64>,
    pub memory: Option<u64>,
}
#[derive(Clone, Debug, Default)]
pub struct Network {
    pub name: String,
    pub wlan_name: String,
    pub ipv4: String,
    pub ipv6: String,
    pub link: u64,
    pub upload: Option<f64>,
    pub download: Option<f64>,
    pub sent: u64,
    pub received: u64,
}
#[derive(Clone, Debug, Default)]
pub struct Sample {
    pub cpu: Option<f64>,
    pub memory_used: u64,
    pub memory_total: u64,
    pub disk: Option<filesystem::Disk>,
    pub read: Option<f64>,
    pub write: Option<f64>,
    pub network: Option<Network>,
    pub processes: Vec<Process>,
    pub processes_available: bool,
    pub error: String,
}
impl Sample {
    pub fn memory_percent(&self) -> Option<f64> {
        percent(self.memory_used, self.memory_total)
    }
    pub fn disk_percent(&self) -> Option<f64> {
        self.disk.as_ref().and_then(|d| percent(d.used, d.total))
    }
}
pub fn percent(used: u64, total: u64) -> Option<f64> {
    (total > 0).then(|| (used as f64 / total as f64 * 100.0).clamp(0.0, 100.0))
}
pub fn rate(before: u64, after: u64, seconds: f64) -> Option<f64> {
    (seconds > 0.0 && seconds <= 5.0 && seconds.is_finite())
        .then(|| after.checked_sub(before).map(|v| v as f64 / seconds))
        .flatten()
}
fn ticks(t: FILETIME) -> u64 {
    (u64::from(t.dwHighDateTime) << 32) | u64::from(t.dwLowDateTime)
}

struct Handle(HANDLE);
impl Drop for Handle {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}
struct DiskIo {
    query: PDH_HQUERY,
    counters: [PDH_HCOUNTER; 2],
    primed: bool,
}
impl DiskIo {
    fn new() -> Option<Self> {
        unsafe {
            let mut query = ptr::null_mut();
            if PdhOpenQueryW(ptr::null(), 0, &mut query) != 0 {
                return None;
            }
            let mut this = Self {
                query,
                counters: [ptr::null_mut(); 2],
                primed: false,
            };
            for (n, direction) in ["Read", "Write"].iter().enumerate() {
                let path = wide(format!(r"\PhysicalDisk(_Total)\Disk {direction} Bytes/sec"));
                if PdhAddEnglishCounterW(query, path.as_ptr(), 0, &mut this.counters[n]) != 0 {
                    return None;
                }
            }
            Some(this)
        }
    }
    fn sample(&mut self) -> [Option<f64>; 2] {
        unsafe {
            if PdhCollectQueryData(self.query) != 0 {
                self.primed = false;
                return [None; 2];
            }
            if !mem::replace(&mut self.primed, true) {
                return [None; 2];
            }
            self.counters.map(|counter| {
                let mut value: PDH_FMT_COUNTERVALUE = mem::zeroed();
                if PdhGetFormattedCounterValue(counter, PDH_FMT_DOUBLE, ptr::null_mut(), &mut value)
                    == 0
                    && value.CStatus <= 1
                    && value.Anonymous.doubleValue.is_finite()
                {
                    Some(value.Anonymous.doubleValue.max(0.0))
                } else {
                    None
                }
            })
        }
    }
}
impl Drop for DiskIo {
    fn drop(&mut self) {
        unsafe {
            PdhCloseQuery(self.query);
        }
    }
}

pub struct Sampler {
    time: Instant,
    cpu: Option<(u64, u64)>,
    processes: HashMap<u32, (u64, u64)>,
    net: Option<(u64, u64, u64)>,
    sent: u64,
    received: u64,
    io: Option<DiskIo>,
    disk: Option<filesystem::Disk>,
    disk_at: Option<Instant>,
}
impl Default for Sampler {
    fn default() -> Self {
        Self {
            time: Instant::now(),
            cpu: None,
            processes: HashMap::new(),
            net: None,
            sent: 0,
            received: 0,
            io: DiskIo::new(),
            disk: None,
            disk_at: None,
        }
    }
}
impl Sampler {
    pub fn pause(&mut self) {
        self.cpu = None;
        self.processes.clear();
        self.net = None;
        self.disk_at = None;
        self.time = Instant::now();
        if let Some(io) = &mut self.io {
            io.primed = false;
        }
    }
    pub fn sample(&mut self, detailed: bool, network_details: bool) -> Sample {
        let seconds = self.time.elapsed().as_secs_f64();
        self.time = Instant::now();
        let mut result = Sample::default();
        unsafe {
            let mut state: MEMORYSTATUSEX = mem::zeroed();
            state.dwLength = mem::size_of_val(&state) as u32;
            if GlobalMemoryStatusEx(&mut state) != 0 {
                result.memory_total = state.ullTotalPhys;
                result.memory_used = state.ullTotalPhys.saturating_sub(state.ullAvailPhys);
            }
            let (mut idle, mut kernel, mut user) = (mem::zeroed(), mem::zeroed(), mem::zeroed());
            // GetSystemTimes only covers the calling processor group on large systems.
            if GetActiveProcessorGroupCount() == 1
                && GetSystemTimes(&mut idle, &mut kernel, &mut user) != 0
            {
                let current = (ticks(idle), ticks(kernel).saturating_add(ticks(user)));
                if let Some((old_idle, old_total)) = self.cpu
                    && seconds <= 5.0
                {
                    result.cpu = current.1.checked_sub(old_total).and_then(|total| {
                        current
                            .0
                            .checked_sub(old_idle)
                            .and_then(|idle| percent(total.saturating_sub(idle), total))
                    });
                }
                self.cpu = Some(current);
            }
            if detailed {
                let (rows, available) = self.process_sample(seconds);
                result.processes = rows;
                result.processes_available = available;
            } else {
                self.processes.clear();
            }
            result.network = self.network(seconds, network_details);
        }
        if self.disk_at.is_none_or(|at| at.elapsed().as_secs() >= 10) {
            let root = format!(
                "{}\\",
                std::env::var("SystemDrive").unwrap_or_else(|_| "C:".into())
            );
            self.disk = filesystem::disk(std::path::Path::new(&root)).ok();
            self.disk_at = Some(Instant::now());
        }
        result.disk = self.disk.clone();
        if let Some(io) = &mut self.io {
            [result.read, result.write] = io.sample();
        }
        if result.memory_total == 0 || result.cpu.is_none() || result.disk.is_none() {
            result.error = "Some counters are unavailable or warming up".into();
        }
        result
    }
    unsafe fn process_sample(&mut self, seconds: f64) -> (Vec<Process>, bool) {
        unsafe {
            let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
            if snapshot == INVALID_HANDLE_VALUE {
                self.processes.clear();
                return (vec![], false);
            }
            let _snapshot = Handle(snapshot);
            let mut entry: PROCESSENTRY32W = mem::zeroed();
            entry.dwSize = mem::size_of_val(&entry) as u32;
            let mut live = HashMap::new();
            let mut rows = Vec::new();
            let cpus = GetActiveProcessorCount(ALL_PROCESSOR_GROUPS).max(1) as f64;
            let mut found = Process32FirstW(snapshot, &mut entry);
            while found != 0 {
                if entry.th32ProcessID != 0 {
                    let mut row = Process {
                        pid: entry.th32ProcessID,
                        name: from_wide(&entry.szExeFile),
                        ..Default::default()
                    };
                    let process = OpenProcess(
                        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ,
                        0,
                        row.pid,
                    );
                    // Protected processes remain visible, with unavailable measurements.
                    let process = if process.is_null() {
                        OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, row.pid)
                    } else {
                        process
                    };
                    if !process.is_null() {
                        let _process = Handle(process);
                        let mut path = [0u16; 32768];
                        let mut length = path.len() as u32;
                        if QueryFullProcessImageNameW(process, 0, path.as_mut_ptr(), &mut length)
                            != 0
                        {
                            row.path =
                                PathBuf::from(String::from_utf16_lossy(&path[..length as usize]));
                        }
                        let (mut created, mut exit, mut kernel, mut user) =
                            (mem::zeroed(), mem::zeroed(), mem::zeroed(), mem::zeroed());
                        if GetProcessTimes(process, &mut created, &mut exit, &mut kernel, &mut user)
                            != 0
                        {
                            let (identity, used) =
                                (ticks(created), ticks(kernel).saturating_add(ticks(user)));
                            if let Some((old_identity, old_used)) = self.processes.get(&row.pid)
                                && *old_identity == identity
                            {
                                row.cpu = rate(*old_used, used, seconds)
                                    .map(|v| (v / 100_000.0 / cpus).clamp(0.0, 100.0));
                            }
                            live.insert(row.pid, (identity, used));
                        }
                        let mut memory: PROCESS_MEMORY_COUNTERS_EX = mem::zeroed();
                        if K32GetProcessMemoryInfo(
                            process,
                            (&mut memory as *mut PROCESS_MEMORY_COUNTERS_EX).cast(),
                            mem::size_of_val(&memory) as u32,
                        ) != 0
                        {
                            row.memory = Some(memory.WorkingSetSize as u64);
                        }
                    }
                    rows.push(row);
                }
                found = Process32NextW(snapshot, &mut entry);
            }
            self.processes = live;
            (rows, true)
        }
    }
    unsafe fn network(&mut self, seconds: f64, network_details: bool) -> Option<Network> {
        unsafe {
            let mut table = ptr::null_mut();
            if GetIfTable2(&mut table) != 0 || table.is_null() {
                self.net = None;
                return None;
            }
            let mut best = 0;
            GetBestInterface(u32::from_ne_bytes([1, 1, 1, 1]), &mut best);
            let rows =
                std::slice::from_raw_parts((*table).Table.as_ptr(), (*table).NumEntries as usize);
            let chosen = rows
                .iter()
                .filter(|r| r.OperStatus == IfOperStatusUp && r.Type != 24)
                .max_by_key(|r| (r.InterfaceIndex == best, r.TransmitLinkSpeed));
            let result = chosen.map(|row| {
                let id = row.InterfaceLuid.Value;
                let mut output = Network {
                    name: from_wide(&row.Alias),
                    wlan_name: if network_details && row.Type == IF_TYPE_IEEE80211 {
                        wlan_name(row.InterfaceGuid).unwrap_or_default()
                    } else {
                        String::new()
                    },
                    link: row.TransmitLinkSpeed,
                    ..Default::default()
                };
                if let Some((old_id, old_sent, old_received)) = self.net
                    && id == old_id
                {
                    output.upload = rate(old_sent, row.OutOctets, seconds);
                    output.download = rate(old_received, row.InOctets, seconds);
                    if output.upload.is_some() {
                        self.sent = self.sent.saturating_add(row.OutOctets - old_sent);
                    }
                    if output.download.is_some() {
                        self.received = self.received.saturating_add(row.InOctets - old_received);
                    }
                }
                self.net = Some((id, row.OutOctets, row.InOctets));
                output.sent = self.sent;
                output.received = self.received;
                (output.ipv4, output.ipv6) = ip_addresses(row.InterfaceIndex);
                output
            });
            FreeMibTable(table.cast());
            if result.is_none() {
                self.net = None;
            }
            result
        }
    }
}
fn wlan_name(adapter: windows_sys::core::GUID) -> Option<String> {
    use windows::{Networking::Connectivity::NetworkInformation, core::GUID};
    let adapter = GUID {
        data1: adapter.data1,
        data2: adapter.data2,
        data3: adapter.data3,
        data4: adapter.data4,
    };
    // GetConnectionProfiles requires package identity on some Windows builds; this EXE is unpackaged.
    let profile = NetworkInformation::GetInternetConnectionProfile().ok()?;
    if profile.NetworkAdapter().ok()?.NetworkAdapterId().ok()? != adapter {
        return None;
    }
    profile
        .WlanConnectionProfileDetails()
        .ok()?
        .GetConnectedSsid()
        .ok()
        .map(|ssid| ssid.to_string())
        .filter(|ssid| !ssid.is_empty())
}
unsafe fn ip_addresses(index: u32) -> (String, String) {
    unsafe {
        let mut size = 0;
        GetAdaptersAddresses(AF_UNSPEC as u32, 0, ptr::null(), ptr::null_mut(), &mut size);
        if size == 0 || size > 1024 * 1024 {
            return Default::default();
        }
        let mut buffer = vec![0u64; (size as usize).div_ceil(8)];
        let first = buffer.as_mut_ptr().cast::<IP_ADAPTER_ADDRESSES_LH>();
        if GetAdaptersAddresses(AF_UNSPEC as u32, 0, ptr::null(), first, &mut size) != 0 {
            return Default::default();
        }
        let mut adapter = first;
        while !adapter.is_null() {
            if (*adapter).Anonymous1.Anonymous.IfIndex == index || (*adapter).Ipv6IfIndex == index {
                let mut result = (String::new(), String::new());
                let mut address = (*adapter).FirstUnicastAddress;
                while !address.is_null() {
                    let socket = (*address).Address.lpSockaddr;
                    let length = (*address).Address.iSockaddrLength;
                    if !socket.is_null() {
                        if (*socket).sa_family == AF_INET
                            && length as usize >= mem::size_of::<SOCKADDR_IN>()
                            && result.0.is_empty()
                        {
                            let v4 = &*socket.cast::<SOCKADDR_IN>();
                            result.0 =
                                std::net::Ipv4Addr::from(v4.sin_addr.S_un.S_addr.to_ne_bytes())
                                    .to_string();
                        } else if (*socket).sa_family == AF_INET6
                            && length as usize >= mem::size_of::<SOCKADDR_IN6>()
                            && result.1.is_empty()
                        {
                            let v6 = &*socket.cast::<SOCKADDR_IN6>();
                            result.1 = std::net::Ipv6Addr::from(v6.sin6_addr.u.Byte).to_string();
                            let scope = v6.Anonymous.sin6_scope_id;
                            if scope != 0 {
                                result.1.push_str(&format!("%{scope}"));
                            }
                        }
                    }
                    address = (*address).Next;
                }
                return result;
            }
            adapter = (*adapter).Next;
        }
        Default::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unpackaged_process_reads_the_current_wlan_name() {
        use windows::Networking::Connectivity::NetworkInformation;
        let Ok(profile) = NetworkInformation::GetInternetConnectionProfile() else {
            return;
        };
        if !profile.IsWlanConnectionProfile().unwrap_or(false) {
            return;
        }
        let Ok(ssid) = profile
            .WlanConnectionProfileDetails()
            .and_then(|d| d.GetConnectedSsid())
        else {
            return;
        };
        let id = profile
            .NetworkAdapter()
            .unwrap()
            .NetworkAdapterId()
            .unwrap();
        let actual = wlan_name(windows_sys::core::GUID {
            data1: id.data1,
            data2: id.data2,
            data3: id.data3,
            data4: id.data4,
        });
        let expected = (!ssid.is_empty()).then(|| ssid.to_string());
        assert_eq!(actual, expected);
    }
    #[test]
    fn wlan_name_does_not_fall_back_to_an_unrelated_network() {
        assert!(wlan_name(windows_sys::core::GUID::from_u128(0)).is_none());
    }
    #[test]
    fn resets_gaps_and_invalid_totals_do_not_fabricate_usage() {
        assert_eq!(rate(100, 250, 1.5), Some(100.0));
        assert_eq!(rate(250, 100, 1.0), None);
        assert_eq!(rate(100, 200, 30.0), None);
        assert_eq!(percent(20, 0), None);
        assert_eq!(percent(25, 100), Some(25.0));
    }
    #[test]
    fn native_read_only_samples_have_finite_counters() {
        let mut sampler = Sampler::default();
        let sample = sampler.sample(true, true);
        assert!(sample.memory_total > 0);
        assert!(sample.processes.iter().any(|p| p.pid == std::process::id()));
        std::thread::sleep(std::time::Duration::from_millis(250));
        let sample = sampler.sample(true, true);
        eprintln!(
            "Read-only monitor probe: processes={}, disk_io_available={}, network_available={}",
            sample.processes.len(),
            sample.read.is_some(),
            sample.network.is_some()
        );
        if let Some(n) = &sample.network {
            assert!(n.ipv4.is_empty() || n.ipv4.parse::<std::net::Ipv4Addr>().is_ok());
            assert!(
                n.ipv6.is_empty()
                    || n.ipv6
                        .split('%')
                        .next()
                        .unwrap()
                        .parse::<std::net::Ipv6Addr>()
                        .is_ok()
            );
        }
        sampler.pause();
        let resumed = sampler.sample(true, true);
        assert!(resumed.cpu.is_none());
        assert!(resumed.processes.iter().all(|p| p.cpu.is_none()));
        assert!(
            resumed
                .network
                .as_ref()
                .is_none_or(|n| n.upload.is_none() && n.download.is_none())
        );
        assert!(
            sample
                .cpu
                .is_none_or(|v| v.is_finite() && (0.0..=100.0).contains(&v))
        );
    }
}
