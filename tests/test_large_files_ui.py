import gc
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.ui import desktop
from cplus_cleaner.features.large_files import large_files as files
from cplus_cleaner.features.large_files import large_files_ui as ui
from cplus_cleaner.application import preferences


class LargePageTests(unittest.TestCase):
    def setUp(self):
        self.gc_enabled = gc.isenabled()
        gc.disable()
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix="large-ui-", dir=core.APP_DIR.parent)
        self.base = Path(self.temp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        for name, size in (("one.zip", 10), ("two.zip", 20), ("video.mp4", 50)):
            (self.root / name).write_bytes(b"x" * size)
        self.disk_info = [dict(root="C:\\", total=100, used=70, free=30), dict(root="D:\\", total=200, used=90, free=110)]
        self.patches = [patch.object(core, "DATA_DIR", self.base / "state"),
            patch.object(core, "installed_apps", return_value=[]),
            patch.object(files, "disk_inventory", return_value=self.disk_info),
            patch.object(files, "default_destination", return_value=r"D:\bigfiles"),
            patch.object(preferences, "read_autostart", return_value=False),
            patch.object(desktop, "disk_snapshot", return_value=self.disk_info[0]),
            patch.object(desktop.messagebox, "showerror")]
        self.mocks = [mock.start() for mock in self.patches]
        self.window = desktop.DesktopApplication()
        self.window.geometry("1160x760+20+20")
        self.window.attributes("-topmost", True)
        self.errors = []
        self.window.report_callback_exception = lambda kind, error, trace: self.errors.append(str(error))
        self.window.navigate("large")
        self.window.update()
        self.page = self.window.views["large"]

    def tearDown(self):
        self.window.cancel.set()
        self.pump()
        for timer in self.window.tk.call("after", "info"):
            self.window.tk.call("after", "cancel", timer)
        self.window.destroy()
        self.mocks[-1].assert_not_called()
        self.assertEqual(self.errors, [])
        for mock in reversed(self.patches):
            mock.stop()
        self.temp.cleanup()
        gc.collect()
        if self.gc_enabled:
            gc.enable()

    def pump(self):
        deadline = time.monotonic() + 8
        self.window.update()
        while self.window.busy and time.monotonic() < deadline:
            self.window.update()
            time.sleep(.01)
        self.window.update()
        self.assertFalse(self.window.busy)

    def scan(self):
        original = files.scan
        with patch.object(files, "scan", side_effect=lambda cancel, progress, **kwargs: original(cancel, progress, root=self.root, minimum=1, blocked=[])):
            self.page.scan_button.invoke()
            self.pump()

    def group(self, name):
        return next(key for key, items in self.page.groups.items() if items[0].category == name)

    def test_landing_is_idle_and_shows_system_drive_and_total_only(self):
        self.assertIsInstance(self.page, ui.LargeFilesPage)
        self.assertFalse(self.window.busy)
        self.assertFalse(self.page.scanned)
        self.assertTrue(self.page.idle.winfo_ismapped())
        self.assertFalse(self.page.results.winfo_ismapped())
        self.assertEqual(self.page.destination.get(), r"D:\bigfiles")
        self.assertFalse(hasattr(self.page, "minimum_combo"))
        self.assertFalse(hasattr(self.page, "minimum"))
        self.assertEqual(len(self.page.disk_bars), 2)
        self.assertEqual(self.page.disk_bars[0].info, self.disk_info[0])
        self.assertEqual(self.page.disk_bars[-1].info["total"], 300)
        self.assertEqual(self.page.disk_bars[-1].info["used"], 160)

    def test_scan_groups_lazy_expansion_default_selection_and_sidebar(self):
        self.scan()
        self.assertEqual(len(self.page.tree.get_children()), 2)
        self.assertEqual(len(self.page.items), 3)
        self.assertEqual(len(self.page.selected), 3)
        self.assertEqual(self.window.nav_metrics["large"]["value"], 80)
        archive = self.group("压缩包")
        box = self.page.tree.bbox(archive, "#0")
        self.page.tree.event_generate("<Button-1>", x=box[0]+27, y=box[1]+box[3]//2)
        self.window.update()
        self.assertEqual(len(self.page.selected), 1, "Click the checkbox, not the category name")
        self.page.tree.event_generate("<Button-1>", x=box[0]+60, y=box[1]+box[3]//2)
        self.window.update()
        self.assertTrue(self.page.tree.item(archive, "open"), "Clicking the category name expands it")
        self.assertEqual(len(self.page.tree.get_children(archive)), 2)
        self.assertEqual(len(self.page.selected), 1)
        self.assertIn("50B", self.page.selection.get())
        self.assertEqual(self.window.nav_metrics["large"]["value"], 80)
        child = self.page.tree.get_children(archive)[0]
        self.page.toggle(child)
        self.assertEqual(len(self.page.selected), 2)
        self.assertEqual(tuple(self.page.tree.item(archive, "image")), (str(self.page.check_images["mixed"]),))

    def test_original_profile_dispatches_strict_threshold_and_protected_rows_stay_visible(self):
        original_scan = files.scan
        def scan(cancel, progress, **kwargs):
            self.assertNotIn("minimum", kwargs)
            self.assertEqual(original_scan.__kwdefaults__["minimum"], files.ORIGINAL_MINIMUM)
            result = original_scan(cancel, progress, root=self.root, minimum=1, blocked=[])
            result["blocked_paths"] = {str(self.root / "one.zip"): "系统目录，仅展示"}
            result["minimum"] = files.ORIGINAL_MINIMUM
            return result
        with patch.object(files, "scan", side_effect=scan):
            self.page.scan_button.invoke()
            self.pump()
        blocked = next(item for item in self.page.items if Path(item.path).name == "one.zip")
        self.assertEqual(self.window.nav_metrics["large"]["value"], 80)
        self.assertNotIn(blocked.path, self.page.selected)
        self.assertIn("大于30MB", self.page.note.get())
        self.page.toggle_all()
        self.page.toggle_all()
        self.assertNotIn(blocked.path, self.page.selected)
        with patch.object(files, "operate") as operation:
            self.page.operate([blocked], "move")
            operation.assert_not_called()
        self.assertIn("仅展示", self.page.note.get())

    def test_single_and_category_delete_use_real_fixture_files_only(self):
        self.scan()
        archive = self.group("压缩包")
        self.page.populate(archive)
        first = self.page.tree.get_children(archive)[0]
        first_path = self.page.row_items[first].path
        with patch.object(ui.messagebox, "askyesno", return_value=True):
            self.page.operate(self.page.requested(first), "delete")
            self.pump()
            self.assertFalse(Path(first_path).exists())
            self.assertEqual(len(self.page.items), 2)
            archive = self.group("压缩包")
            self.page.operate(self.page.requested(archive), "delete")
            self.pump()
        self.assertEqual([item.category for item in self.page.items], ["视频文件"])
        self.assertEqual(self.window.nav_metrics["large"]["value"], 50)

    def test_batch_move_keeps_failed_items_and_updates_capacity(self):
        self.scan()
        archive = self.group("压缩包")
        destination = self.base / "target"
        destination.mkdir()
        def target(value, source):
            return destination / source.name, destination
        with patch.object(files, "destination_path", side_effect=target):
            self.page.operate(self.page.requested(archive), "move")
            self.pump()
        self.assertEqual(len(self.page.items), 1)
        self.assertEqual(self.window.nav_badges["large"].cget("text"), "50B")
        self.assertTrue((destination / "one.zip").exists())
        self.assertTrue((destination / "two.zip").exists())
        self.assertTrue((self.root / "video.mp4").exists())

    def test_progress_visible_cancelled_scan_stays_below_100(self):
        ready, release = threading.Event(), threading.Event()
        def scan(cancel, progress, **kwargs):
            progress(dict(stage="scan", message="正在检查测试文件", completed=None, total=None))
            ready.set()
            release.wait(4)
            return dict(items=[], minimum=20*files.MIB, counts={}, cancelled=cancel.is_set())
        with patch.object(files, "scan", side_effect=scan):
            self.page.scan()
            try:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    self.window.update()
                    if ready.is_set() and "测试文件" in self.page.progress_text.get():
                        break
                    time.sleep(.01)
                self.assertTrue(self.page.page_progress.winfo_ismapped())
                self.assertEqual(str(self.page.page_progress.cget("mode")), "indeterminate")
                self.window.cancel.set()
            finally:
                release.set()
            self.pump()
        self.assertLess(self.page.progress_value.get(), 100)
        self.assertIn("取消", self.page.progress_text.get())

    def test_history_window_is_reused_and_can_be_reopened_after_close(self):
        with patch.object(files, "records", return_value=[]) as records:
            first = self.page.show_history()
            first.withdraw()
            self.assertIs(self.page.show_history(), first)
            self.assertIs(self.page.show_history(), first)
            self.window.update()
            windows = [child for child in self.page.winfo_children() if isinstance(child, ui.tk.Toplevel)]
            self.assertEqual(windows, [first])
            self.assertEqual(first.state(), "normal")
            self.assertEqual(records.call_count, 3)
            first.destroy()
            self.assertIsNone(self.page._history_window)
            reopened = self.page.show_history()
            self.assertIsNot(reopened, first)
            reopened.destroy()

    def test_actions_and_table_fit_minimum_size(self):
        self.window.geometry("980x650+20+20")
        self.scan()
        for widget in (self.page.rescan_button, self.page.move_button, self.page.delete_button, self.page.footer, self.page.tree):
            self.assertTrue(widget.winfo_ismapped())
            self.assertGreaterEqual(widget.winfo_rootx(), self.page.winfo_rootx())
            self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(), self.page.winfo_rootx()+self.page.winfo_width())
        archive = self.group("压缩包")
        self.page.populate(archive)
        self.page.tree.item(archive, open=True)
        self.window.update()
        box = self.page.tree.bbox(archive, "#3")
        self.assertTrue(box)
        with patch.object(self.page, "operate") as operation:
            self.page.tree.event_generate("<Button-1>", x=box[0]+box[2]//2, y=box[1]+box[3]//2)
            self.window.update()
            operation.assert_called_once_with(self.page.groups[archive], "delete")


if __name__ == "__main__":
    unittest.main()

# Purpose: Regression tests for large-file groups and file list UI
