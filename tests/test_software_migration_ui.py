import gc
from pathlib import Path
import tempfile
import time
import threading
import weakref
import tkinter as tk
import unittest
from unittest.mock import patch

import core
import desktop
import preferences
import software_migration as migration
import software_migration_ui as ui
from ui_widgets import check_images


class SoftwarePageTests(unittest.TestCase):
    def setUp(self):
        self.gc_enabled = gc.isenabled()
        gc.disable()
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix="software-ui-test-", dir=core.APP_DIR.parent)
        self.base = Path(self.temp.name)
        self.entries = []
        for name, amount in (("Example One", 9), ("Example Two", 27)):
            path = self.base / name
            path.mkdir()
            (path / "app.exe").write_bytes(b"x" * amount)
            self.entries.append(dict(name=name, command=str(path / "app.exe"), location=str(path)))
        self.destination = self.base / "destination"
        self.destination.mkdir()
        self.patches = [patch.object(core, "DATA_DIR", self.base / "state"),
            patch.object(core, "installed_apps", return_value=self.entries),
            patch.object(migration, "system_drive", return_value=Path(self.base.anchor)),
            patch.object(migration, "user_program_roots", return_value=[]),
            patch.object(migration, "default_destination", return_value=str(self.destination)),
            patch.object(migration.windows, "running_images", return_value=[]),
            patch.object(migration.windows, "service_images", return_value=[]),
            patch.object(migration.windows, "filesystem", return_value="NTFS"),
            patch.object(migration.windows, "extract_icon", return_value=None),
            patch.object(preferences, "read_autostart", return_value=False),
            patch.object(desktop, "disk_snapshot", return_value=dict(total=100, free=30, used=70)),
            patch.object(ui, "disk_snapshot", return_value=dict(total=100, free=30, used=70)),
            patch.object(desktop.messagebox, "showerror")]
        self.mocks = [mock.start() for mock in self.patches]
        self.window = desktop.DesktopApplication()
        self.window.geometry("1160x760+20+20")
        self.window.attributes("-topmost", True)
        self.window.update()

    def tearDown(self):
        self.window.cancel.set()
        self.pump()
        for timer in self.window.tk.call("after", "info"):
            self.window.tk.call("after", "cancel", timer)
        self.window.destroy()
        self.mocks[-1].assert_not_called()
        for mock in reversed(self.patches):
            mock.stop()
        self.temp.cleanup()
        gc.collect()
        if self.gc_enabled:
            gc.enable()

    def pump(self):
        self.window.update()
        deadline = time.monotonic() + 10
        while self.window.busy and time.monotonic() < deadline:
            self.window.update()
            time.sleep(.01)
        self.window.update()
        self.assertFalse(self.window.busy)

    def open(self, scan=True):
        self.window.nav_widgets["migrate"][2].event_generate("<Button-1>")
        self.pump()
        page = self.window.views["migrate"]
        if scan:
            page.scan_button.invoke()
            self.pump()
        return page

    def test_sidebar_waits_for_manual_scan_and_calculates_selection(self):
        page = self.open(scan=False)
        self.mocks[1].assert_not_called()
        self.assertFalse(self.window.busy)
        self.assertIn('尚未扫描', page.empty.cget('text'))
        page.scan_button.invoke()
        self.pump()
        self.assertIsInstance(page, ui.SoftwareMigrationPage)
        self.assertEqual(len(page.rows), 2)
        self.assertEqual(str(next(iter(page.rows.values())).checkbox.cget("selectimage")), str(check_images(self.window)["on"]))
        self.assertIn("2 款", page.heading.get())
        self.assertEqual(page.selected, set())
        self.assertFalse(page.move_button.enabled)
        self.assertEqual(self.window.nav_metrics["migrate"]["value"], 36)
        page.select_all_button.invoke()
        self.assertEqual(len(page.selected), 2)
        self.assertIn("36B", page.selection.get())
        self.assertTrue(page.move_button.enabled)
        page.rows[page.items[0].key].checkbox.invoke()
        self.assertEqual(len(page.selected), 1)
        self.assertIn("27B", page.selection.get())
        self.assertEqual(self.window.nav_metrics["migrate"]["value"], 36)

    def test_row_and_batch_move_only_dispatch_requested_software(self):
        page = self.open()
        first = page.items[0]
        def moved(item, destination, cancel, progress, **kwargs):
            return dict(ok=True, released=item.size)
        with patch.object(migration, "move", side_effect=moved) as move:
            page.rows[first.key].action.invoke()
            self.pump()
            self.assertEqual(move.call_args.args[0], first)
            self.assertEqual(move.call_args.args[1], str(self.destination))
            self.assertEqual(len(page.items), 1)
            page.select_all_button.invoke()
            page.move_button.invoke()
            self.pump()
            self.assertEqual(move.call_count, 2)
        self.assertEqual(page.items, [])
        self.assertFalse(page.move_button.enabled)
        self.assertEqual(self.window.nav_metrics["migrate"]["value"], 0)
        self.assertTrue(all((Path(entry["location"]) / "app.exe").exists() for entry in self.entries))

    def test_sidebar_reentry_keeps_results_until_manual_scan(self):
        page = self.open()
        self.window.home_page.results['migrate'] = dict(value=36, payload=page.scan_summary, error='')
        self.window.home_page.adopted.add('migrate')
        self.mocks[1].return_value = self.entries[:1]
        self.window.navigate('home')
        self.assertIs(self.open(scan=False), page)
        self.assertEqual(len(page.items), 2)
        self.mocks[1].assert_called_once()
        self.assertIn('migrate', self.window.home_page.results)
        page.scan_button.invoke()
        self.pump()
        self.assertEqual(len(page.items), 1)
        self.assertEqual(self.mocks[1].call_count, 2)
        self.assertEqual(self.window.nav_metrics['migrate']['value'], 9)

    def test_scan_progress_is_visible_and_cancellation_does_not_show_100_percent(self):
        ready, release = threading.Event(), threading.Event()
        def scan(cancel, progress, on_batch, *, on_progress):
            on_progress(dict(stage="scan", completed=1, total=4, message="正在扫描样本软件"))
            ready.set()
            release.wait(5)
            return dict(items=[], skipped={}, excluded_services=0, cancelled=cancel.is_set())
        with patch.object(migration, "scan", side_effect=scan):
            self.window.navigate("migrate")
            page = self.window.views["migrate"]
            page.scan_button.invoke()
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    self.window.update()
                    if ready.is_set() and page.progress_value.get() == 25:
                        break
                    time.sleep(.01)
                self.assertTrue(page.page_progress.winfo_ismapped())
                self.assertTrue(self.window.busy)
                self.assertEqual(page.progress_value.get(), 25)
                self.assertIn("1/4", page.progress_text.get())
                self.window.cancel.set()
            finally:
                release.set()
                self.pump()
        self.assertIn("已取消", page.progress_text.get())
        self.assertLess(page.progress_value.get(), 100)

    def test_move_progress_shows_batch_index_and_real_bytes_until_completion(self):
        page = self.open()
        ready, release = threading.Event(), threading.Event()
        def moved(item, destination, cancel, progress, *, on_progress):
            on_progress(dict(stage="copy", completed=3, total=10, message="正在复制样本软件"))
            ready.set()
            release.wait(5)
            return dict(ok=True, released=item.size)
        with patch.object(migration, "move", side_effect=moved):
            page.rows[page.items[0].key].action.invoke()
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    self.window.update()
                    if ready.is_set() and page.progress_value.get() == 30:
                        break
                    time.sleep(.01)
                self.assertTrue(page.page_progress.winfo_ismapped())
                self.assertEqual(page.progress_value.get(), 30)
                self.assertIn("第 1/1 款", page.progress_text.get())
                self.assertIn("3B / 10B", page.progress_text.get())
            finally:
                release.set()
                self.pump()
        self.assertEqual(page.progress_value.get(), 100)
        self.assertIn("搬家完成", page.progress_text.get())

    def test_running_software_is_visible_but_cannot_be_selected_or_moved(self):
        with patch.object(migration.windows, "running_images", return_value=[Path(self.entries[0]["location"]) / "app.exe"]):
            page = self.open()
        blocked = page.rows[page.items[0].key]
        self.assertFalse(blocked.action.enabled)
        self.assertEqual(blocked.checkbox.cget("state"), "disabled")
        self.assertIn("运行中", blocked.item.blocked)
        page.select_all_button.invoke()
        self.assertEqual(len(page.selected), 1)
        self.assertNotIn(blocked.item.key, page.selected)
        self.assertEqual(self.window.nav_metrics["migrate"]["value"], 27)

    def test_service_software_never_appears_or_contributes_to_sidebar_capacity(self):
        service = Path(self.entries[0]["location"]) / "app.exe"
        with patch.object(migration.windows, "service_images", return_value=[f'"{service}" --service']):
            page = self.open()
        self.assertEqual(len(page.items), 1)
        self.assertEqual(page.items[0].name, "Example Two")
        self.assertEqual(len(page.rows), 1)
        self.assertIn("已排除 1 个含系统服务的软件", page.note.get())
        self.assertEqual(self.window.nav_metrics["migrate"]["value"], 27)

    def test_sorting_destination_history_and_rescan(self):
        page = self.open()
        page.sort("size")
        self.assertEqual([row.item.size for row in page.rows.values()], [9, 27])
        other = self.base / "chosen-folder"
        other.mkdir()
        with patch.object(ui.filedialog, "askdirectory", return_value=str(other)):
            page.choose_button.invoke()
        self.assertEqual(page.destination.get(), str(other))
        self.assertIn(str(other).replace("\\", "\\\\"), (core.DATA_DIR / "software-migration-settings.json").read_text(encoding="utf-8"))
        migration.append_record(dict(id="history", name="Old app", source="C:\\App", target="D:\\App", time=time.time(), status="搬家完成", ok=True, released=9))
        page.history_button.invoke()
        windows = [child for child in page.winfo_children() if isinstance(child, tk.Toplevel)]
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].title(), "软件搬家记录")
        first_window = windows[0]
        first_window.withdraw()
        migration.append_record(dict(id="new-history", name="New app", source="C:\\New", target="D:\\New", time=time.time(), status="搬家完成", ok=True, released=27))
        page.history_button.invoke()
        page.history_button.invoke()
        self.window.update()
        windows = [child for child in page.winfo_children() if isinstance(child, tk.Toplevel)]
        self.assertEqual(windows, [first_window], "Repeated clicks must reuse the history window")
        self.assertEqual(first_window.state(), "normal")
        table = next(child for child in first_window.winfo_children() if isinstance(child, ui.ttk.Treeview))
        self.assertEqual(len(table.get_children()), 2, "Reopening must refresh records")
        windows[0].destroy()
        self.assertIsNone(page._history_window)
        page.history_button.invoke()
        self.assertIsNot(page._history_window, first_window)
        page._history_window.destroy()
        page.select_all_button.invoke()
        page.scan_button.invoke()
        self.pump()
        self.assertFalse(page.selected)
        self.assertEqual(len(page.rows), 2)

    def test_minimum_window_keeps_row_actions_target_and_batch_move_visible(self):
        self.window.geometry("980x650+20+20")
        page = self.open()
        self.assertIs(page.warning_image, self.window.images.get("status-warning.png"))
        self.assertEqual((page.warning_image.width(), page.warning_image.height()), (44, 44))
        self.assertLessEqual(page.warning.winfo_rootx()+page.warning.winfo_width(), page.heading_label.winfo_rootx())
        self.assertGreaterEqual(page.heading_label.winfo_width(), page.heading_label.winfo_reqwidth())
        self.assertLessEqual(page.heading_label.winfo_rootx()+page.heading_label.winfo_width(), page.scan_button.winfo_rootx())
        for button in (page.warning, page.scan_button, page.move_button, page.choose_button, page.history_button, page.page_progress, next(iter(page.rows.values())).action):
            x, y = button.winfo_rootx() + button.winfo_width() // 2, button.winfo_rooty() + button.winfo_height() // 2
            self.assertEqual(self.window.winfo_containing(x, y), button)

    def test_resize_refits_software_names_and_rescan_returns_to_first_row(self):
        page = self.open()
        for row in page.rows.values():
            self.assertEqual(row.name.cget("text"), row.item.name)
        self.window.geometry("980x650+20+20")
        self.window.update()
        self.window.geometry("1160x760+20+20")
        self.window.update()
        for row in page.rows.values():
            self.assertEqual(row.name.cget("text"), row.item.name)
        page.canvas.yview_moveto(1)
        old_row = next(iter(page.rows.values()))
        old_variable = weakref.ref(old_row.value)
        page.render()
        self.window.update()
        self.assertIsNone(old_variable(), "Destroyed rows must release Tcl variables on the UI thread")
        self.assertEqual(page.canvas.yview()[0], 0)


if __name__ == "__main__":
    unittest.main()
