import ctypes as ct
from ctypes import wintypes as wt
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cplus_cleaner.features.popup import popup_windows as windows


class FakeWin32:
    """All window and process calls are in-memory; no native windows are touched."""

    def __init__(self):
        self.hwnd = 0x100001234 if ct.sizeof(ct.c_void_p) == 8 else 0x1234
        self.handle = self.hwnd + 10
        self.created = 0x01DA112233445566
        self.window = {"pid": 8877, "image": r"C:\Apps\Example\example.exe",
                       "title": "", "class": "ExamplePopup", "width": 420, "height": 260,
                       "visible": True, "root": self.hwnd, "style": -0x80000000}
        self.present = True
        self.process_accessible = True
        self.image_readable = True
        self.time_readable = True
        self.user = SimpleNamespace(
            EnumWindows=Mock(side_effect=self.enumerate),
            IsWindow=Mock(side_effect=lambda hwnd: self.present and hwnd == self.hwnd),
            IsWindowVisible=Mock(side_effect=lambda hwnd: self.window["visible"]),
            GetAncestor=Mock(side_effect=lambda hwnd, flags: self.window["root"]),
            GetWindowThreadProcessId=Mock(side_effect=self.pid),
            GetWindowTextLengthW=Mock(side_effect=lambda hwnd: len(self.window["title"])),
            GetWindowTextW=Mock(side_effect=lambda hwnd, buffer, size: self.string(buffer, self.window["title"])),
            GetClassNameW=Mock(side_effect=lambda hwnd, buffer, size: self.string(buffer, self.window["class"])),
            GetWindowRect=Mock(side_effect=self.rectangle),
            GetWindowLongW=Mock(side_effect=lambda hwnd, index: self.window["style"]),
            PostMessageW=Mock(return_value=True),
        )
        self.kernel = SimpleNamespace(
            OpenProcess=Mock(side_effect=lambda rights, inherit, pid: self.handle if self.process_accessible else 0),
            CloseHandle=Mock(return_value=True),
            QueryFullProcessImageNameW=Mock(side_effect=self.image),
            GetProcessTimes=Mock(side_effect=self.times),
        )
        self.api = windows._Win32(self.user, self.kernel)
        self.api.own_pid = 9999

    def enumerate(self, callback, unused):
        callback(self.hwnd, 0)
        return True

    @staticmethod
    def string(buffer, value):
        buffer.value = value
        return len(value)

    def pid(self, hwnd, output):
        output._obj.value = self.window["pid"]
        return 1

    def rectangle(self, hwnd, output):
        output._obj.left, output._obj.top = 10, 20
        output._obj.right = 10 + self.window["width"]
        output._obj.bottom = 20 + self.window["height"]
        return True

    def image(self, handle, flags, buffer, length):
        if not self.image_readable:
            return False
        buffer.value = self.window["image"]
        length._obj.value = len(buffer.value)
        return True

    def times(self, handle, created, exited, kernel, user):
        if not self.time_readable:
            return False
        created._obj.dwLowDateTime = self.created & 0xffffffff
        created._obj.dwHighDateTime = self.created >> 32
        return True


class WindowAdapterTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeWin32()
        self.api = self.native.api
        self.backend = patch.object(windows, "_backend", return_value=self.api)
        self.backend.start()
        self.addCleanup(self.backend.stop)

    def test_snapshot_preserves_pointer_width_process_identity_and_empty_title(self):
        items = windows.snapshot()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0], {"hwnd": self.native.hwnd, "pid": 8877,
            "created": self.native.created, "image": self.native.window["image"],
            "title": "", "class": "ExamplePopup", "width": 420, "height": 260,
            "popup": True})
        self.native.kernel.OpenProcess.assert_called_once_with(0x1000, False, 8877)
        self.native.kernel.CloseHandle.assert_called_once_with(self.native.handle)
        self.native.user.PostMessageW.assert_not_called()

    def test_every_native_call_has_explicit_signature_and_pointer_safe_handles(self):
        for library in (self.native.user, self.native.kernel):
            for function in vars(library).values():
                self.assertTrue(function.argtypes)
                self.assertIsNotNone(function.restype)
        self.assertIs(self.native.kernel.OpenProcess.restype, wt.HANDLE)
        self.assertIs(self.native.user.GetAncestor.restype, wt.HWND)
        self.assertIs(self.native.user.GetWindowLongW.restype, wt.LONG)
        self.assertIs(self.native.user.PostMessageW.argtypes[0], wt.HWND)
        self.assertEqual(ct.sizeof(self.native.user.PostMessageW.argtypes[2]), ct.sizeof(ct.c_void_p))
        self.assertEqual(ct.sizeof(self.native.user.PostMessageW.argtypes[3]), ct.sizeof(ct.c_void_p))

    def test_self_hidden_child_and_desktop_windows_are_excluded(self):
        for key, value in (("pid", 9999), ("visible", False), ("root", self.native.hwnd + 1),
                           ("class", "Shell_TrayWnd"), ("class", "Shell_SecondaryTrayWnd"),
                           ("class", "Progman"), ("class", "WorkerW"), ("class", "#32769")):
            with self.subTest(key=key, value=value):
                previous = self.native.window[key]
                self.native.window[key] = value
                self.assertEqual(windows.snapshot(), [])
                self.native.window[key] = previous
        self.native.kernel.OpenProcess.assert_not_called()
        self.native.user.PostMessageW.assert_not_called()

    def test_missing_executable_or_creation_identity_is_not_actionable(self):
        for attribute in ("process_accessible", "image_readable", "time_readable"):
            with self.subTest(attribute=attribute):
                setattr(self.native, attribute, False)
                self.assertIsNone(windows.describe(self.native.hwnd))
                setattr(self.native, attribute, True)
        self.native.window["image"] = "relative.exe"
        self.assertIsNone(windows.describe(self.native.hwnd))
        self.native.window["image"] = r"C:\Apps\example.exe"
        self.native.created = 0
        self.assertIsNone(windows.describe(self.native.hwnd))
        self.assertEqual(self.native.kernel.CloseHandle.call_count, 4)

    def test_process_handle_is_released_on_observation_failure(self):
        self.native.kernel.QueryFullProcessImageNameW.side_effect = OSError("access denied")
        self.assertIsNone(windows.describe(self.native.hwnd))
        self.native.kernel.CloseHandle.assert_called_once_with(self.native.handle)

    def test_changed_owner_during_observation_is_discarded(self):
        count = 0
        def changing_pid(hwnd, output):
            nonlocal count
            count += 1
            output._obj.value = 8877 if count == 1 else 8878
            return 1
        self.native.user.GetWindowThreadProcessId.side_effect = changing_pid
        self.assertIsNone(windows.describe(self.native.hwnd))

    def test_failed_enumeration_raises_instead_of_looking_like_empty_desktop(self):
        self.native.user.EnumWindows.side_effect = None
        self.native.user.EnumWindows.return_value = False
        with self.assertRaisesRegex(OSError, "EnumWindows failed"):
            windows.snapshot()
        self.native.kernel.OpenProcess.assert_not_called()

    def test_disappearance_is_confirmed_by_window_absence_visibility_or_identity_change(self):
        item = windows.describe(self.native.hwnd)
        self.assertFalse(windows.disappeared(item))
        self.native.present = False
        self.assertTrue(windows.disappeared(item))
        self.native.present = True
        self.native.window["visible"] = False
        self.assertTrue(windows.disappeared(item))
        self.native.window["visible"] = True
        self.native.window["pid"] += 1
        self.assertTrue(windows.disappeared(item))
        self.native.window["pid"] = item["pid"]
        self.native.created += 1
        self.assertTrue(windows.disappeared(item))
        self.native.user.PostMessageW.assert_not_called()

    def test_inaccessible_window_metadata_does_not_confirm_disappearance(self):
        item = windows.describe(self.native.hwnd)
        for attribute in ("process_accessible", "time_readable"):
            with self.subTest(attribute=attribute):
                setattr(self.native, attribute, False)
                self.assertEqual(windows.snapshot(), [])
                self.assertFalse(windows.disappeared(item))
                setattr(self.native, attribute, True)
        # Confirmation needs no image/title/class queries, only process identity.
        self.native.image_readable = False
        self.assertEqual(windows.snapshot(), [])
        self.assertFalse(windows.disappeared(item))
        self.native.created += 1
        self.assertTrue(windows.disappeared(item))
        self.native.user.GetWindowThreadProcessId.side_effect = None
        self.native.user.GetWindowThreadProcessId.return_value = 0
        self.assertFalse(windows.disappeared(item))
        self.native.user.PostMessageW.assert_not_called()

    def test_disappearance_handles_native_errors_conservatively_and_releases_handles(self):
        item = windows.describe(self.native.hwnd)
        self.native.kernel.CloseHandle.reset_mock()
        self.native.kernel.GetProcessTimes.side_effect = OSError("access denied")
        self.assertFalse(windows.disappeared(item))
        self.native.kernel.CloseHandle.assert_called_once_with(self.native.handle)
        self.native.user.IsWindow.side_effect = OSError("desktop unavailable")
        self.assertFalse(windows.disappeared(item))
        self.native.user.PostMessageW.assert_not_called()

    def test_matching_snapshot_posts_only_wm_close_after_rechecking(self):
        item = windows.describe(self.native.hwnd)
        item["rule_id"] = "parent-metadata"
        self.assertTrue(windows.post_close(item))
        self.native.user.PostMessageW.assert_called_once_with(self.native.hwnd, 0x10, 0, 0)
        self.assertEqual(self.native.kernel.OpenProcess.call_count, 2)

    def test_non_popup_windows_are_observed_and_changed_popup_style_prevents_close(self):
        popup = windows.describe(self.native.hwnd)
        self.assertTrue(popup["popup"])
        self.native.window["style"] = 0
        ordinary = windows.snapshot()[0]
        self.assertIs(ordinary["popup"], False)
        self.assertFalse(windows.post_close(popup))
        self.native.user.PostMessageW.assert_not_called()
        self.assertTrue(windows.post_close(ordinary))
        self.native.user.GetWindowLongW.assert_called_with(self.native.hwnd, -16)

    def test_style_query_distinguishes_valid_zero_from_native_error(self):
        self.native.window["style"] = 0
        self.assertIs(windows.describe(self.native.hwnd)["popup"], False)
        def style_error(hwnd, index):
            ct.set_last_error(5)
            return 0
        self.native.user.GetWindowLongW.side_effect = style_error
        self.assertIsNone(windows.describe(self.native.hwnd))
        self.native.user.PostMessageW.assert_not_called()

    def test_any_changed_window_or_process_field_prevents_close(self):
        item = windows.describe(self.native.hwnd)
        for field in windows._FIELDS:
            with self.subTest(field=field):
                changed = dict(item)
                if type(changed[field]) is bool:
                    changed[field] = not changed[field]
                else:
                    changed[field] = changed[field] + 1 if type(changed[field]) is int else changed[field] + "changed"
                self.assertFalse(windows.post_close(changed))
        self.native.user.PostMessageW.assert_not_called()

    def test_disappeared_window_and_failed_post_are_not_reported_as_closed(self):
        item = windows.describe(self.native.hwnd)
        self.native.present = False
        self.assertFalse(windows.post_close(item))
        self.native.user.PostMessageW.assert_not_called()
        self.native.present = True
        self.native.user.PostMessageW.return_value = False
        self.assertFalse(windows.post_close(item))
        self.native.user.PostMessageW.side_effect = OSError("access denied")
        self.assertFalse(windows.post_close(item))

    def test_invalid_inputs_do_not_reach_native_window_calls(self):
        for hwnd in (None, 0, -1, True, 1.0, "123", windows._POINTER_MAX + 1):
            self.assertIsNone(windows.describe(hwnd))
        for item in (None, 1, {}, {"hwnd": self.native.hwnd}):
            self.assertFalse(windows.post_close(item))
            self.assertFalse(windows.disappeared(item))
        valid = windows.describe(self.native.hwnd)
        for key, value in (("pid", True), ("created", 1.0), ("title", None), ("hwnd", True), ("popup", 1)):
            self.assertFalse(windows.post_close(dict(valid, **{key: value})))
        for key, value in (("pid", True), ("created", 1.0), ("hwnd", True)):
            self.assertFalse(windows.disappeared(dict(valid, **{key: value})))
        self.native.user.PostMessageW.assert_not_called()


if __name__ == "__main__":
    unittest.main()
