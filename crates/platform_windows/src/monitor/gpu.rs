//! Read-only GPU temperature probe via the WDDM D3DKMT adapter performance data.
//! The kernel reports deci-Celsius, which we convert and range-check before use.
use std::{ffi::c_void, mem};
use windows_sys::Win32::{
    Foundation::HANDLE,
    Graphics::Gdi::{CreateDCW, DeleteDC},
    System::LibraryLoader::{GetProcAddress, LoadLibraryA},
};

type D3dkmtHandle = u32;

#[repr(C)]
struct OpenAdapterFromHdc {
    hdc: HANDLE,
    adapter: D3dkmtHandle,
    luid_low: u32,
    luid_high: i32,
    vidpn_source_id: u32,
}

#[repr(C)]
struct QueryAdapterInfo {
    adapter: D3dkmtHandle,
    query_type: i32,
    data: *mut c_void,
    data_size: u32,
}

#[repr(C)]
#[derive(Default)]
struct AdapterPerfData {
    physical_adapter_index: u32,
    memory_frequency: u64,
    max_memory_frequency: u64,
    max_memory_frequency_oc: u64,
    memory_bandwidth: u64,
    pcie_bandwidth: u64,
    fan_rpm: u32,
    power: u32,
    temperature: u32,
    power_state_override: u8,
}

type OpenFn = unsafe extern "system" fn(*mut OpenAdapterFromHdc) -> i32;
type QueryFn = unsafe extern "system" fn(*const QueryAdapterInfo) -> i32;
type CloseFn = unsafe extern "system" fn(*const u32) -> i32;

const KMTQAITYPE_ADAPTERPERFDATA: i32 = 62;

/// Read the primary GPU temperature in Celsius when the driver reports it.
///
/// Returns `None` on any unsupported or malformed reading so callers can fall
/// back to another sensor source.
pub fn read() -> Option<f64> {
    unsafe {
        let gdi = LoadLibraryA(c"gdi32.dll".as_ptr().cast::<u8>());
        if gdi.is_null() {
            return None;
        }
        let open: OpenFn = std::mem::transmute(GetProcAddress(
            gdi,
            c"D3DKMTOpenAdapterFromHdc".as_ptr().cast::<u8>(),
        )?);
        let query: QueryFn = std::mem::transmute(GetProcAddress(
            gdi,
            c"D3DKMTQueryAdapterInfo".as_ptr().cast::<u8>(),
        )?);
        let close: CloseFn = std::mem::transmute(GetProcAddress(
            gdi,
            c"D3DKMTCloseAdapter".as_ptr().cast::<u8>(),
        )?);

        let display: Vec<u16> = "DISPLAY\0".encode_utf16().collect();
        let hdc = CreateDCW(
            display.as_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            std::ptr::null(),
        );
        if hdc.is_null() {
            return None;
        }
        let mut open_data: OpenAdapterFromHdc = mem::zeroed();
        open_data.hdc = hdc;
        let opened = open(&mut open_data) == 0;
        DeleteDC(hdc);
        if !opened {
            return None;
        }
        let mut perf = AdapterPerfData {
            physical_adapter_index: 0,
            ..Default::default()
        };
        let status = query(&QueryAdapterInfo {
            adapter: open_data.adapter,
            query_type: KMTQAITYPE_ADAPTERPERFDATA,
            data: (&mut perf as *mut AdapterPerfData).cast(),
            data_size: mem::size_of::<AdapterPerfData>() as u32,
        });
        close(&open_data.adapter);
        if status != 0 || perf.power_state_override == 0 {
            return None;
        }
        let celsius = perf.temperature as f64 / 10.0;
        (celsius > 0.0 && celsius <= 125.0).then_some(celsius)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_probe_returns_only_valid_temperatures() {
        if let Some(value) = read() {
            assert!(value.is_finite() && value > 0.0 && value <= 125.0);
            eprintln!("D3DKMT GPU temperature: {value:.1}C");
        }
    }
}
