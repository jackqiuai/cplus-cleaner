"""Visible top-level window observations and exact-snapshot WM_CLOSE requests.

No hooks, injection, process termination, or window hiding are used here.  A
successful close request means Windows accepted WM_CLOSE, not that it closed.
"""
from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
from functools import lru_cache
import ntpath
import os


_FIELDS = ("hwnd", "pid", "created", "image", "title", "class", "width", "height", "popup")
_EXCLUDED_CLASSES = frozenset(name.casefold() for name in (
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "#32769",
))
_POINTER_MAX = (1 << (ct.sizeof(ct.c_void_p) * 8)) - 1
_ENUM_PROC = getattr(ct, "WINFUNCTYPE", ct.CFUNCTYPE)(wt.BOOL, wt.HWND, ct.c_ssize_t)


def _valid_hwnd(hwnd):
    return type(hwnd) is int and 0 < hwnd <= _POINTER_MAX


class _Win32:
    """Small injectable Win32 boundary; construction does not observe windows."""

    def __init__(self, user=None, kernel=None):
        self.user = user if user is not None else ct.WinDLL("user32", use_last_error=True)
        self.kernel = kernel if kernel is not None else ct.WinDLL("kernel32", use_last_error=True)
        self.own_pid = os.getpid()
        signatures = (
            (self.user.EnumWindows, [_ENUM_PROC, ct.c_ssize_t], wt.BOOL),
            (self.user.IsWindow, [wt.HWND], wt.BOOL),
            (self.user.IsWindowVisible, [wt.HWND], wt.BOOL),
            (self.user.GetAncestor, [wt.HWND, wt.UINT], wt.HWND),
            (self.user.GetWindowThreadProcessId, [wt.HWND, ct.POINTER(wt.DWORD)], wt.DWORD),
            (self.user.GetWindowTextLengthW, [wt.HWND], ct.c_int),
            (self.user.GetWindowTextW, [wt.HWND, wt.LPWSTR, ct.c_int], ct.c_int),
            (self.user.GetClassNameW, [wt.HWND, wt.LPWSTR, ct.c_int], ct.c_int),
            (self.user.GetWindowRect, [wt.HWND, ct.POINTER(wt.RECT)], wt.BOOL),
            (self.user.GetWindowLongW, [wt.HWND, ct.c_int], wt.LONG),
            (self.user.PostMessageW, [wt.HWND, wt.UINT, ct.c_size_t, ct.c_ssize_t], wt.BOOL),
            (self.kernel.OpenProcess, [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            (self.kernel.CloseHandle, [wt.HANDLE], wt.BOOL),
            (self.kernel.QueryFullProcessImageNameW,
             [wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD)], wt.BOOL),
            (self.kernel.GetProcessTimes, [wt.HANDLE] + [ct.POINTER(wt.FILETIME)] * 4, wt.BOOL),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result

    def _pid(self, hwnd):
        pid = wt.DWORD()
        if not self.user.GetWindowThreadProcessId(hwnd, ct.byref(pid)):
            return 0
        return pid.value

    def _process(self, pid):
        process = self.kernel.OpenProcess(0x1000, False, pid)
        if not process:
            return None
        try:
            path, length = ct.create_unicode_buffer(32768), wt.DWORD(32768)
            if not self.kernel.QueryFullProcessImageNameW(process, 0, path, ct.byref(length)):
                return None
            if not path.value or not ntpath.isabs(path.value):
                return None
            identity = self._created(process)
            return (path.value, identity) if identity else None
        finally:
            self.kernel.CloseHandle(process)

    def _created(self, process):
        created, exited, kernel, user = (wt.FILETIME() for _ in range(4))
        if not self.kernel.GetProcessTimes(process, ct.byref(created), ct.byref(exited),
                                           ct.byref(kernel), ct.byref(user)):
            return None
        return ((created.dwHighDateTime << 32) | created.dwLowDateTime) or None

    def describe(self, hwnd):
        if not _valid_hwnd(hwnd):
            return None
        user = self.user
        try:
            if (not user.IsWindow(hwnd) or not user.IsWindowVisible(hwnd)
                    or user.GetAncestor(hwnd, 2) != hwnd):
                return None
            pid = self._pid(hwnd)
            if not pid or pid == self.own_pid:
                return None
            kind = ct.create_unicode_buffer(512)
            if not user.GetClassNameW(hwnd, kind, len(kind)):
                return None
            if kind.value.casefold() in _EXCLUDED_CLASSES:
                return None
            ct.set_last_error(0)
            length = user.GetWindowTextLengthW(hwnd)
            if length < 0 or length > 32767 or (not length and ct.get_last_error()):
                return None
            title = ct.create_unicode_buffer(length + 1)
            ct.set_last_error(0)
            if not user.GetWindowTextW(hwnd, title, len(title)) and ct.get_last_error():
                return None
            rect = wt.RECT()
            if not user.GetWindowRect(hwnd, ct.byref(rect)):
                return None
            ct.set_last_error(0)
            style = user.GetWindowLongW(hwnd, -16)
            if not style and ct.get_last_error():
                return None
            process = self._process(pid)
            if process is None:
                return None
            # The window may have disappeared or changed owners during observation.
            if (not user.IsWindow(hwnd) or not user.IsWindowVisible(hwnd)
                    or user.GetAncestor(hwnd, 2) != hwnd or self._pid(hwnd) != pid):
                return None
            return {"hwnd": hwnd, "pid": pid, "created": process[1], "image": process[0],
                    "title": title.value, "class": kind.value,
                    "width": rect.right - rect.left, "height": rect.bottom - rect.top,
                    "popup": bool(style & 0x80000000)}
        except OSError:
            return None

    def snapshot(self):
        handles = []
        @_ENUM_PROC
        def collect(hwnd, unused):
            handles.append(hwnd)
            return True
        ct.set_last_error(0)
        if not self.user.EnumWindows(collect, 0):
            raise OSError(ct.get_last_error(), "EnumWindows failed")
        result = []
        for hwnd in dict.fromkeys(handles):
            item = self.describe(hwnd)
            if item is not None:
                result.append(item)
        return result

    def post_close(self, hwnd):
        return bool(self.user.PostMessageW(hwnd, 0x0010, 0, 0))

    def disappeared(self, item):
        """Confirm the old window identity is gone; unreadable metadata is unknown."""
        hwnd = item["hwnd"]
        try:
            if not self.user.IsWindow(hwnd) or not self.user.IsWindowVisible(hwnd):
                return True
            pid = self._pid(hwnd)
            if not pid:
                return False
            if pid != item["pid"]:
                return True
            process = self.kernel.OpenProcess(0x1000, False, pid)
            if not process:
                return False
            try:
                created = self._created(process)
                return created is not None and created != item["created"]
            finally:
                self.kernel.CloseHandle(process)
        except OSError:
            return False


@lru_cache(maxsize=1)
def _backend():
    return _Win32() if os.name == "nt" else None


def snapshot() -> list[dict]:
    """Observe accessible visible top-level windows; inaccessible owners are skipped."""
    api = _backend()
    return api.snapshot() if api is not None else []


def describe(hwnd: int) -> dict | None:
    """Read one current visible top-level window, without sending any messages."""
    if not _valid_hwnd(hwnd):
        return None
    api = _backend()
    return api.describe(hwnd) if api is not None else None


def disappeared(item: dict) -> bool:
    """Read-only confirmation of disappearance, not absence from an enumeration."""
    if (not isinstance(item, dict) or not _valid_hwnd(item.get("hwnd"))
            or type(item.get("pid")) is not int or item["pid"] <= 0
            or type(item.get("created")) is not int or item["created"] <= 0):
        return False
    try:
        api = _backend()
        return api.disappeared(item) if api is not None else False
    except OSError:
        return False


def post_close(item: dict) -> bool:
    """Request WM_CLOSE only while all observed identity and window fields match."""
    if not isinstance(item, dict) or not all(key in item for key in _FIELDS):
        return False
    expected = {key: item[key] for key in _FIELDS}
    if (not _valid_hwnd(expected["hwnd"])
            or any(type(expected[key]) is not int for key in ("pid", "created", "width", "height"))
            or expected["pid"] <= 0 or expected["created"] <= 0
            or type(expected["popup"]) is not bool
            or any(type(expected[key]) is not str for key in ("image", "title", "class"))):
        return False
    api = _backend()
    if api is None or api.describe(expected["hwnd"]) != expected:
        return False
    try:
        return api.post_close(expected["hwnd"])
    except OSError:
        return False


def enrich(item, points=()):
    """Extend a still-identical legacy snapshot; failure is unknown, not a match."""
    if not isinstance(item, dict) or not all(k in item for k in _FIELDS):
        return None
    expected = {key: item[key] for key in _FIELDS}
    if (not _valid_hwnd(expected['hwnd'])
            or any(type(expected[key]) is not int for key in ('pid', 'created', 'width', 'height'))
            or expected['pid'] <= 0 or expected['created'] <= 0
            or type(expected['popup']) is not bool
            or any(type(expected[key]) is not str for key in ('image', 'title', 'class'))):
        return None
    try:
        api = _backend()
        if api is None or api.describe(expected['hwnd']) != expected:
            return None
        from popup_native import backend
        current = backend().details(expected, points)
        if api.describe(expected['hwnd']) != expected:
            return None
        return current
    except (OSError, AttributeError, ValueError):
        return None


def vendor_support_reason():
    if os.name != 'nt':
        return '当前系统不支持 Windows 窗口检测'
    try:
        from popup_native import backend
        backend()  # Resolve APIs only; no window observation or actions.
    except (OSError, AttributeError):
        return '当前系统缺少显示器/DPI 检测接口'
    return ''


def apply_vendor(item, action, validator, points=()):
    """Repeat all rule conditions immediately before the explicitly chosen action."""
    if action not in ('close', 'hide', 'kill', 'close_hide') or not callable(validator):
        return False
    current = enrich(item, points)
    if current is None or not validator(current):
        return False
    try:
        api = _backend()
        if api is None:
            return False
        from popup_native import backend
        expected = {key: current[key] for key in _FIELDS}
        if api.describe(expected['hwnd']) != expected:
            return False
        return backend().apply(api, expected, action)
    except (OSError, AttributeError, ValueError):
        return False
