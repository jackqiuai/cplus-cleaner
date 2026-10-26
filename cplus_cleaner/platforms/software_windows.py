"""Read-only Windows observations for software relocation; no process execution."""
from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
import os
from pathlib import Path
import struct
import zlib


def administrator():
    return os.name == "nt" and bool(ct.windll.shell32.IsUserAnAdmin())


def filesystem(path):
    if os.name != "nt":
        return ""
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    kernel.GetVolumePathNameW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.DWORD]
    kernel.GetVolumeInformationW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.DWORD,
        ct.POINTER(wt.DWORD), ct.POINTER(wt.DWORD), ct.POINTER(wt.DWORD), wt.LPWSTR, wt.DWORD]
    root, name = ct.create_unicode_buffer(1024), ct.create_unicode_buffer(64)
    if not kernel.GetVolumePathNameW(str(path), root, len(root)):
        raise ct.WinError(ct.get_last_error())
    if not kernel.GetVolumeInformationW(root, None, 0, None, None, None, name, len(name)):
        raise ct.WinError(ct.get_last_error())
    return name.value


def running_images():
    if os.name != "nt":
        return []
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    class Entry(ct.Structure):
        _fields_ = [("size", wt.DWORD), ("usage", wt.DWORD), ("pid", wt.DWORD),
                    ("heap", ct.c_size_t), ("module", wt.DWORD), ("threads", wt.DWORD),
                    ("parent", wt.DWORD), ("priority", wt.LONG), ("flags", wt.DWORD),
                    ("exe", wt.WCHAR * 260)]
    kernel.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wt.HANDLE
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.Process32FirstW.argtypes = [wt.HANDLE, ct.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wt.HANDLE, ct.POINTER(Entry)]
    kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD)]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ct.c_void_p(-1).value:
        raise ct.WinError(ct.get_last_error())
    result = []
    try:
        entry = Entry()
        entry.size = ct.sizeof(entry)
        available = kernel.Process32FirstW(snapshot, ct.byref(entry))
        while available:
            process = kernel.OpenProcess(0x1000, False, entry.pid)
            if process:
                try:
                    buffer, length = ct.create_unicode_buffer(32768), wt.DWORD(32768)
                    if kernel.QueryFullProcessImageNameW(process, 0, buffer, ct.byref(length)):
                        result.append(Path(buffer.value))
                finally:
                    kernel.CloseHandle(process)
            available = kernel.Process32NextW(snapshot, ct.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return result


def service_images():
    if os.name != "nt":
        return []
    import winreg
    result = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services") as key:
        for index in range(winreg.QueryInfoKey(key)[0]):
            try:
                with winreg.OpenKey(key, winreg.EnumKey(key, index)) as service:
                    result.append(winreg.QueryValueEx(service, "ImagePath")[0])
            except OSError:
                continue
    return result


def fixed_drives():
    if os.name != "nt":
        return []
    mask = ct.windll.kernel32.GetLogicalDrives()
    return [Path(f"{chr(65+i)}:\\") for i in range(26)
            if mask & (1 << i) and ct.windll.kernel32.GetDriveTypeW(f"{chr(65+i)}:\\") == 3]


def extract_icon(path, index=0):
    """Render a static EXE/DLL/ICO resource to a 32px PNG using Win32 and zlib."""
    if os.name != "nt" or not path or not Path(path).is_file():
        return None
    shell, user, gdi = ct.windll.shell32, ct.windll.user32, ct.windll.gdi32
    shell.ExtractIconExW.argtypes = [wt.LPCWSTR, ct.c_int, ct.POINTER(wt.HICON), ct.POINTER(wt.HICON), wt.UINT]
    user.DrawIconEx.argtypes = [wt.HDC, ct.c_int, ct.c_int, wt.HICON, ct.c_int, ct.c_int, wt.UINT, wt.HBRUSH, wt.UINT]
    user.DestroyIcon.argtypes = [wt.HICON]
    gdi.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi.CreateCompatibleDC.restype = wt.HDC
    gdi.CreateDIBSection.argtypes = [wt.HDC, ct.c_void_p, wt.UINT, ct.POINTER(ct.c_void_p), wt.HANDLE, wt.DWORD]
    gdi.CreateDIBSection.restype = wt.HBITMAP
    gdi.SelectObject.argtypes = [wt.HDC, wt.HANDLE]
    gdi.SelectObject.restype = wt.HANDLE
    gdi.DeleteObject.argtypes = [wt.HANDLE]
    gdi.DeleteDC.argtypes = [wt.HDC]
    icon, bitmap, dc, old = wt.HICON(), None, None, None
    try:
        if not shell.ExtractIconExW(str(path), index, ct.byref(icon), None, 1) or not icon.value:
            return None
        dc = gdi.CreateCompatibleDC(None)
        header = ct.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, 32, -32, 1, 32, 0, 4096, 0, 0, 0, 0) + b"\0" * 4)
        pixels = ct.c_void_p()
        bitmap = gdi.CreateDIBSection(dc, header, 0, ct.byref(pixels), None, 0)
        if not bitmap:
            return None
        old = gdi.SelectObject(dc, bitmap)
        # White background also handles legacy icons without an alpha channel.
        ct.memset(pixels, 255, 4096)
        if not user.DrawIconEx(dc, 0, 0, icon, 32, 32, 0, None, 3):
            return None
        raw = ct.string_at(pixels, 4096)
        rgb = bytearray()
        for y in range(32):
            rgb.append(0)
            for x in range(32):
                b, g, r, _ = raw[(y*32+x)*4:(y*32+x+1)*4]
                rgb.extend((r, g, b))
        def chunk(name, data):
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rgb)) + chunk(b"IEND", b"")
    finally:
        if old:
            gdi.SelectObject(dc, old)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if dc:
            gdi.DeleteDC(dc)
        if icon.value:
            user.DestroyIcon(icon)
