$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32;
[ComVisible(true), Guid("6E793361-73C6-11D0-8469-00AA00442901"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface ICacheProgress {
    [PreserveSig] int ScanProgress(ulong used, uint flags, [MarshalAs(UnmanagedType.LPWStr)] string status);
    [PreserveSig] int PurgeProgress(ulong freed, ulong total, uint flags, [MarshalAs(UnmanagedType.LPWStr)] string status);
}
[ComVisible(true), ClassInterface(ClassInterfaceType.None)]
public sealed class CacheProgress : ICacheProgress {
    public int ScanProgress(ulong used, uint flags, string status) { return 0; }
    public int PurgeProgress(ulong freed, ulong total, uint flags, string status) { return 0; }
}
[ComImport, Guid("8FCE5227-04DA-11D1-A004-00805F8ABE06"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface ICacheHandler {
    [PreserveSig] int Initialize(IntPtr key, [MarshalAs(UnmanagedType.LPWStr)] string volume, out IntPtr name, out IntPtr description, ref uint flags);
    [PreserveSig] int GetSpaceUsed(out ulong bytes, ICacheProgress callback);
    [PreserveSig] int Purge(ulong bytes, ICacheProgress callback);
    [PreserveSig] int ShowProperties(IntPtr window);
    [PreserveSig] int Deactivate(out uint flags);
}
public static class WindowsCache {
    static void Check(int hr) { if (hr != 0) throw new COMException("Windows cleanup handler returned " + hr.ToString("X8"), hr); }
    public static ulong Run(string kind, string volume, bool purge) {
        if (kind != "Delivery Optimization Files" && kind != "Update Cleanup" && kind != "Previous Installations") throw new ArgumentException("Unsupported cleanup handler");
        using (var machine = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
        using (var key = machine.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VolumeCaches\" + kind, false)) {
            if (key == null) throw new InvalidOperationException("Windows cleanup handler is unavailable");
            object instance = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid((string)key.GetValue(null)), true));
            ICacheHandler handler = null;
            bool initialized = false;
            try {
                handler = (ICacheHandler)instance;
                uint flags = 0; // Never use EVCF_SETTINGSMODE: it may clean during initialization.
                IntPtr name = IntPtr.Zero, description = IntPtr.Zero, button = IntPtr.Zero;
                int hr;
                try { hr = handler.Initialize(key.Handle.DangerousGetHandle(), volume, out name, out description, ref flags); }
                finally { Marshal.FreeCoTaskMem(name); Marshal.FreeCoTaskMem(description); Marshal.FreeCoTaskMem(button); }
                if (hr == 1) return 0;
                Check(hr); initialized = true;
                var callback = new CacheProgress();
                ulong bytes;
                Check(handler.GetSpaceUsed(out bytes, callback));
                if (bytes == ulong.MaxValue) throw new InvalidOperationException("Windows could not determine cleanup size");
                if (purge && bytes > 0) Check(handler.Purge(ulong.MaxValue, callback));
                GC.KeepAlive(callback);
                return bytes;
            } finally {
                try { if (initialized) { uint ignored; handler.Deactivate(out ignored); } }
                finally { Marshal.FinalReleaseComObject(instance); }
            }
        }
    }
}
'@
$kind = '__KIND__'
$volume = $env:SystemDrive.TrimEnd('\')
if ($kind -eq 'batch') {
    $results = foreach ($item in @('Delivery Optimization Files', 'Update Cleanup', 'Previous Installations')) {
        try {
            $bytes = [WindowsCache]::Run($item, $volume, $false)
            @{ kind = $item; before = $bytes; after = $bytes }
        } catch {
            @{ kind = $item; error = $_.Exception.Message }
        }
    }
    ConvertTo-Json -InputObject @($results) -Compress
    return
}
$purge = '__MODE__' -eq 'purge'
$before = [WindowsCache]::Run($kind, $volume, $purge)
$after = $before
if ($purge) { $after = [WindowsCache]::Run($kind, $volume, $false) }
@{ before = $before; after = $after } | ConvertTo-Json -Compress
