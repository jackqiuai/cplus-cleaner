"""Extended Win32 observations and guarded optional popup actions.

Legacy observations keep their original schema. Physical coordinates are read
inside a temporary per-monitor DPI context, restored on every exit path.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as ct
from ctypes import wintypes as wt
from functools import lru_cache
import ntpath


class MonitorInfo(ct.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('rcMonitor', wt.RECT), ('rcWork', wt.RECT), ('dwFlags', wt.DWORD)]


def _cdiv(a, b):
    if not b:
        raise ValueError('invalid monitor dimensions')
    return abs(a) // abs(b) * (-1 if (a < 0) != (b < 0) else 1)


def position(monitor, rect):
    left, top, right, bottom = monitor
    x, y, x2, y2 = rect
    width, height = right-left, bottom-top
    if width < 3 or height < 2 or x2 <= x or y2 <= y:
        raise ValueError('invalid window/monitor rectangle')
    center_x, center_y = x+(x2-x)//2, y+(y2-y)//2
    column = _cdiv(center_x-left, width//3)
    if column == 1:
        return 0 if abs(width-(x2-x)) < 20 and abs(height-(y2-y)) < 20 else 3
    if column not in (0, 2):
        return -1
    upper = _cdiv(center_y-top, height//2) == 0 or center_y < top+height//2
    return (1 if upper else 2) if column == 0 else (4 if upper else 5)


def logical_size(value, scale):
    if type(value) is not int or type(scale) is not int or value < 0 or not 50 <= scale <= 500:
        raise ValueError('invalid scale or dimension')
    return (value*100 + scale//2)//scale


class Native:
    def __init__(self, user=None, shcore=None, gdi=None, kernel=None):
        self.user = user if user is not None else ct.WinDLL('user32', use_last_error=True)
        self.shcore = shcore if shcore is not None else ct.WinDLL('shcore', use_last_error=True)
        self.gdi = gdi if gdi is not None else ct.WinDLL('gdi32', use_last_error=True)
        self.kernel = kernel if kernel is not None else ct.WinDLL('kernel32', use_last_error=True)
        signatures = (
            (self.user.SetThreadDpiAwarenessContext, [ct.c_void_p], ct.c_void_p),
            (self.user.MonitorFromWindow, [wt.HWND, wt.DWORD], wt.HANDLE),
            (self.user.GetMonitorInfoW, [wt.HANDLE, ct.POINTER(MonitorInfo)], wt.BOOL),
            (self.user.GetWindowRect, [wt.HWND, ct.POINTER(wt.RECT)], wt.BOOL),
            (self.user.GetWindowLongW, [wt.HWND, ct.c_int], wt.LONG),
            (self.user.WindowFromPoint, [wt.POINT], wt.HWND),
            (self.user.GetAncestor, [wt.HWND, wt.UINT], wt.HWND),
            (self.user.GetDC, [wt.HWND], wt.HDC),
            (self.user.ReleaseDC, [wt.HWND, wt.HDC], ct.c_int),
            (self.user.ShowWindow, [wt.HWND, ct.c_int], wt.BOOL),
            (self.shcore.GetScaleFactorForMonitor, [wt.HANDLE, ct.POINTER(ct.c_int)], ct.c_long),
            (self.gdi.GetPixel, [wt.HDC, ct.c_int, ct.c_int], wt.DWORD),
            (self.kernel.TerminateProcess, [wt.HANDLE, wt.UINT], wt.BOOL),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result

    @contextmanager
    def physical_coordinates(self):
        previous = self.user.SetThreadDpiAwarenessContext(ct.c_void_p(-4))
        if not previous:
            raise OSError('无法设置显示器 DPI 上下文')
        try:
            yield
        finally:
            self.user.SetThreadDpiAwarenessContext(previous)

    def details(self, item, points):
        hwnd = item['hwnd']
        with self.physical_coordinates():
            rect = wt.RECT()
            if not self.user.GetWindowRect(hwnd, ct.byref(rect)):
                return None
            monitor = self.user.MonitorFromWindow(hwnd, 2)
            info = MonitorInfo(); info.cbSize = ct.sizeof(info)
            scale = ct.c_int()
            if (not monitor or not self.user.GetMonitorInfoW(monitor, ct.byref(info))
                    or self.shcore.GetScaleFactorForMonitor(monitor, ct.byref(scale)) < 0):
                return None
            monitor_rect = tuple(getattr(info.rcMonitor, k) for k in ('left', 'top', 'right', 'bottom'))
            physical_rect = tuple(getattr(rect, k) for k in ('left', 'top', 'right', 'bottom'))
            values = {}
            for name, index in (('styles', -16), ('exstyles', -20)):
                ct.set_last_error(0)
                value = self.user.GetWindowLongW(hwnd, index)
                if not value and ct.get_last_error():
                    return None
                values[name] = value & 0xffffffff
            width, height = rect.right-rect.left, rect.bottom-rect.top
            values.update(position=position(monitor_rect, physical_rect), physical_rect=physical_rect,
                          monitor_rect=monitor_rect, scale=scale.value,
                          logical_width=logical_size(width, scale.value),
                          logical_height=logical_size(height, scale.value), pixels={})
            if points:
                # Sample only exposed target pixels. Covered/inaccessible points
                # remain unknown; never substitute another window's colors.
                dc = self.user.GetDC(None)
                if not dc:
                    return None
                try:
                    for x, y in sorted(set(points)):
                        if not (0 <= x < width and 0 <= y < height):
                            return None
                        target = self.user.WindowFromPoint(wt.POINT(rect.left+x, rect.top+y))
                        if not target or self.user.GetAncestor(target, 2) != hwnd:
                            return None
                        color = self.gdi.GetPixel(dc, rect.left+x, rect.top+y)
                        if color == 0xffffffff:
                            return None
                        values['pixels'][x, y] = (0xff000000 | ((color & 255) << 16)
                                                 | (color & 0xff00) | ((color >> 16) & 255))
                finally:
                    self.user.ReleaseDC(None, dc)
            return dict(item, **values)

    def kill(self, api, item):
        # Bind termination to the handle's creation time and image, not PID alone.
        process = api.kernel.OpenProcess(0x101001, False, item['pid'])
        if not process:
            return False
        try:
            image, size = ct.create_unicode_buffer(32768), wt.DWORD(32768)
            if (api._created(process) != item['created']
                    or not api.kernel.QueryFullProcessImageNameW(process, 0, image, ct.byref(size))
                    or ntpath.normcase(image.value) != ntpath.normcase(item['image'])
                    or api._pid(item['hwnd']) != item['pid']):
                return False
            return bool(self.kernel.TerminateProcess(process, 0))
        finally:
            api.kernel.CloseHandle(process)

    def apply(self, api, item, action):
        if action == 'kill':
            return self.kill(api, item)
        accepted = False
        if action in ('close', 'close_hide'):
            accepted = api.post_close(item['hwnd'])
        if action in ('hide', 'close_hide'):
            # Recheck after a close request; the HWND may already be gone/reused.
            current = api.describe(item['hwnd'])
            if current != item:
                return accepted
            self.user.ShowWindow(item['hwnd'], 0)
            accepted = not api.user.IsWindowVisible(item['hwnd']) or accepted
        return accepted


@lru_cache(maxsize=1)
def backend():
    return Native()

# Purpose: Native popup matching and blocking adapters
