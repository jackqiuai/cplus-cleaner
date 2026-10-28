"""Atomic Windows desktop instance guard, with a pending activation event."""
from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
import os

INSTANCE_NAME = r'Local\CCCLEANER.Desktop.v1'
WINDOW_TITLE = 'CPlus Cleaner · C盘清理助手'


class SingleInstance:
    def __init__(self, name=INSTANCE_NAME, *, activate=True):
        self.primary = True
        self.mutex = self.event = None
        self.root = self.timer = None
        if os.name != 'nt':
            return
        self.kernel = ct.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ct.c_void_p, wt.BOOL, wt.LPCWSTR]
        self.kernel.CreateMutexW.restype = wt.HANDLE
        self.kernel.CreateEventW.argtypes = [ct.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
        self.kernel.CreateEventW.restype = wt.HANDLE
        self.kernel.SetEvent.argtypes = [wt.HANDLE]
        self.kernel.SetEvent.restype = wt.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        self.kernel.WaitForSingleObject.restype = wt.DWORD
        self.kernel.CloseHandle.argtypes = [wt.HANDLE]
        self.kernel.CloseHandle.restype = wt.BOOL
        try:
            # Create the event before competing for the mutex. A second launch
            # can signal it even while the first launch is still constructing Tk.
            self.event = self.kernel.CreateEventW(None, False, False, name+'.activate')
            if not self.event:
                raise ct.WinError(ct.get_last_error())
            self.mutex = self.kernel.CreateMutexW(None, False, name)
            existed = ct.get_last_error() == 183
            if not self.mutex:
                raise ct.WinError(ct.get_last_error())
            self.primary = not existed
            if not self.primary and activate:
                self._allow_foreground()
                if not self.kernel.SetEvent(self.event):
                    raise ct.WinError(ct.get_last_error())
        except BaseException:
            self.close()
            raise

    def _allow_foreground(self):
        user = ct.WinDLL('user32', use_last_error=True)
        user.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
        user.FindWindowW.restype = wt.HWND
        user.GetWindowThreadProcessId.argtypes = [wt.HWND, ct.POINTER(wt.DWORD)]
        user.AllowSetForegroundWindow.argtypes = [wt.DWORD]
        hwnd = user.FindWindowW(None, WINDOW_TITLE) or user.FindWindowW(None, "CPlus Cleaner · Drive Cleanup Assistant")
        if hwnd:
            pid = wt.DWORD()
            user.GetWindowThreadProcessId(hwnd, ct.byref(pid))
            user.AllowSetForegroundWindow(pid)

    def activation_pending(self):
        return bool(self.primary and self.event and self.kernel.WaitForSingleObject(self.event, 0) == 0)

    def attach(self, root, restore):
        self.root = root
        def poll():
            self.timer = None
            if self.activation_pending():
                restore()
            if self.mutex:
                self.timer = root.after(100, poll)
        root.bind('<Destroy>', lambda event: self.close() if event.widget is root else None, add='+')
        self.timer = root.after(0, poll)

    def close(self):
        if self.timer is not None and self.root is not None:
            try:
                self.root.after_cancel(self.timer)
            except Exception:
                pass
            self.timer = None
        for name in ('mutex', 'event'):
            handle = getattr(self, name, None)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, name, None)
        self.root = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def run(background=False, *, name=INSTANCE_NAME):
    # Imports and all Tk creation follow the atomic guard, so duplicate starts
    # exit without creating even a transient second root window.
    with SingleInstance(name, activate=not background) as instance:
        if not instance.primary:
            return
        from cplus_cleaner.common.user_data import migrate_legacy_data
        try:
            migrate_legacy_data()
        except OSError:
            from cplus_cleaner.localization import i18n
            from cplus_cleaner.localization.localized_tk import messagebox
            i18n.set_language('')
            messagebox.showerror('用户数据迁移未完成',
                '无法迁移用户数据。请关闭旧版程序，检查新旧数据目录及权限后重试。原有数据不会被覆盖。')
            return
        from cplus_cleaner.ui.desktop import DesktopApplication
        window = DesktopApplication(background=background)
        instance.attach(window, window.restore_window)
        window.mainloop()

# Purpose: Single-instance launch and activation
