"""Native Windows tray icon. Its message thread only posts events to Tk's queue."""
from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
import os
import threading
import uuid
from cplus_cleaner.localization.i18n import tr


class NativeTray:
    def __init__(self, events, icon_path):
        self.events, self.icon_path = events, str(icon_path)
        self.ready = threading.Event()
        self.hwnd, self.error, self.thread = None, None, None

    def start(self):
        if self.hwnd:
            return True
        if os.name != "nt":
            self.error = "系统托盘仅支持 Windows"
            return False
        self.thread = threading.Thread(target=self._run, name="CCCLEANER-tray", daemon=True)
        self.thread.start()
        if not self.ready.wait(3):
            self.error = "系统托盘未响应"
        return bool(self.hwnd) and not self.error

    def stop(self):
        if self.hwnd:
            ct.windll.user32.PostMessageW(wt.HWND(self.hwnd), 0x10, 0, 0)
        if self.thread:
            self.thread.join(timeout=2)

    def refresh_language(self):
        if self.hwnd:
            ct.windll.user32.PostMessageW(wt.HWND(self.hwnd), 0x8002, 0, 0)

    def _run(self):
        user = ct.WinDLL("user32", use_last_error=True)
        shell = ct.WinDLL("shell32", use_last_error=True)
        kernel = ct.WinDLL("kernel32", use_last_error=True)
        proc_type = ct.WINFUNCTYPE(ct.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WindowClass(ct.Structure):
            _fields_ = [("style", wt.UINT), ("proc", proc_type), ("cls_extra", ct.c_int),
                        ("win_extra", ct.c_int), ("instance", wt.HINSTANCE), ("icon", wt.HICON),
                        ("cursor", wt.HANDLE), ("background", wt.HBRUSH),
                        ("menu", wt.LPCWSTR), ("name", wt.LPCWSTR)]

        class GUID(ct.Structure):
            _fields_ = [("a", wt.DWORD), ("b", wt.WORD), ("c", wt.WORD), ("d", ct.c_byte * 8)]

        class IconData(ct.Structure):
            _fields_ = [("size", wt.DWORD), ("hwnd", wt.HWND), ("id", wt.UINT),
                        ("flags", wt.UINT), ("callback", wt.UINT), ("icon", wt.HICON),
                        ("tip", wt.WCHAR * 128), ("state", wt.DWORD), ("state_mask", wt.DWORD),
                        ("info", wt.WCHAR * 256), ("version", wt.UINT), ("title", wt.WCHAR * 64),
                        ("info_flags", wt.DWORD), ("guid", GUID), ("balloon", wt.HICON)]

        user.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user.DefWindowProcW.restype = ct.c_ssize_t
        user.RegisterClassW.argtypes = [ct.POINTER(WindowClass)]
        user.RegisterClassW.restype = wt.ATOM
        user.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ct.c_int, ct.c_int, ct.c_int, ct.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        user.CreateWindowExW.restype = wt.HWND
        user.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ct.c_int, ct.c_int, wt.UINT]
        user.LoadImageW.restype = wt.HANDLE
        user.LoadIconW.argtypes = [wt.HINSTANCE, ct.c_void_p]
        user.LoadIconW.restype = wt.HICON
        user.CreatePopupMenu.restype = wt.HMENU
        user.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ct.c_size_t, wt.LPCWSTR]
        user.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ct.c_int, ct.c_int, ct.c_int, wt.HWND, ct.c_void_p]
        user.TrackPopupMenu.restype = wt.UINT
        user.DestroyMenu.argtypes = [wt.HMENU]
        user.SetForegroundWindow.argtypes = [wt.HWND]
        user.DestroyWindow.argtypes = [wt.HWND]
        user.DestroyIcon.argtypes = [wt.HICON]
        user.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
        user.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
        user.RegisterWindowMessageW.restype = wt.UINT
        user.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user.GetMessageW.argtypes = [ct.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
        user.GetMessageW.restype = wt.BOOL
        user.TranslateMessage.argtypes = [ct.POINTER(wt.MSG)]
        user.DispatchMessageW.argtypes = [ct.POINTER(wt.MSG)]
        user.DispatchMessageW.restype = ct.c_ssize_t
        shell.Shell_NotifyIconW.argtypes = [wt.DWORD, ct.POINTER(IconData)]
        shell.Shell_NotifyIconW.restype = wt.BOOL
        kernel.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        kernel.GetModuleHandleW.restype = wt.HMODULE
        callback_id = 0x8001
        taskbar_created = user.RegisterWindowMessageW("TaskbarCreated")
        data, icon, hwnd, registered = IconData(), None, None, False
        class_name = "CCCLEANERTray_" + uuid.uuid4().hex
        instance = kernel.GetModuleHandleW(None)

        def send(action):
            self.events.put(("tray", action))

        def window_proc(window, message, wparam, lparam):
            if message == 0x8002:
                data.tip = tr("CPlus Cleaner · 点击打开，右键设置或退出")
                shell.Shell_NotifyIconW(1, ct.byref(data))
                return 0
            if message == callback_id:
                notification = lparam & 0xffff
                if notification in (0x202, 0x203, 0x400, 0x401):  # mouse or keyboard activation
                    send("restore")
                elif notification in (0x205, 0x7b):
                    menu = user.CreatePopupMenu()
                    try:
                        for number, label in ((1, "打开 CPlus Cleaner"), (2, "设置"), (3, "退出软件")):
                            user.AppendMenuW(menu, 0, number, tr(label))
                        point = wt.POINT()
                        user.GetCursorPos(ct.byref(point))
                        user.SetForegroundWindow(window)
                        command = user.TrackPopupMenu(menu, 0x100 | 0x2, point.x, point.y, 0, window, None)
                        if command:
                            send({1: "restore", 2: "settings", 3: "exit"}[command])
                    finally:
                        user.DestroyMenu(menu)
                        user.PostMessageW(window, 0, 0, 0)
                return 0
            if message == taskbar_created:
                shell.Shell_NotifyIconW(0, ct.byref(data))
                shell.Shell_NotifyIconW(4, ct.byref(data))
                return 0
            if message == 0x10:
                user.DestroyWindow(window)
                return 0
            if message == 0x2:
                shell.Shell_NotifyIconW(2, ct.byref(data))
                user.PostQuitMessage(0)
                return 0
            return user.DefWindowProcW(window, message, wparam, lparam)

        proc = proc_type(window_proc)
        try:
            wc = WindowClass()
            wc.proc, wc.instance, wc.name = proc, instance, class_name
            if not user.RegisterClassW(ct.byref(wc)):
                raise ct.WinError(ct.get_last_error())
            registered = True
            hwnd = user.CreateWindowExW(0, class_name, "CCCLEANER Tray", 0, 0, 0, 0, 0, None, None, instance, None)
            if not hwnd:
                raise ct.WinError(ct.get_last_error())
            icon = user.LoadImageW(None, self.icon_path, 1, 32, 32, 0x10)
            data.size, data.hwnd, data.id = ct.sizeof(data), hwnd, 1
            data.flags, data.callback = 1 | 2 | 4, callback_id
            data.icon = icon or user.LoadIconW(None, ct.c_void_p(32512))
            data.tip = tr("CPlus Cleaner · 点击打开，右键设置或退出")
            if not shell.Shell_NotifyIconW(0, ct.byref(data)):
                raise OSError("无法创建系统托盘图标")
            data.version = 4
            shell.Shell_NotifyIconW(4, ct.byref(data))
            self.hwnd = hwnd
            self.ready.set()
            message = wt.MSG()
            while user.GetMessageW(ct.byref(message), None, 0, 0) > 0:
                user.TranslateMessage(ct.byref(message))
                user.DispatchMessageW(ct.byref(message))
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.hwnd = None
            self.ready.set()
            if hwnd and user.IsWindow(wt.HWND(hwnd)):
                user.DestroyWindow(hwnd)
            if icon:
                user.DestroyIcon(icon)
            if registered:
                user.UnregisterClassW(class_name, instance)

# Purpose: Windows tray icon and menu
