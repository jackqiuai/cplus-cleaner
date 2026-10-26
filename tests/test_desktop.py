"""Desktop workflows over workspace-only files and mocked Windows observations.

These are real Tk integration tests. Run serially with other GUI tests, because
desktop hit testing and screenshots share the same Windows desktop.
"""
from pathlib import Path
import gc
import json
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.ui import desktop
from cplus_cleaner.application import preferences
from cplus_cleaner.ui.ui_widgets import Checkbutton, check_images


class DesktopWorkflowTests(unittest.TestCase):
    def setUp(self):
        # Baseline captions must not depend on the runner's Windows language.
        language = patch.object(desktop.i18n, 'detect_language', return_value='zh')
        language.start()
        self.addCleanup(language.stop)
        self.gc_enabled = gc.isenabled()
        gc.disable()
        gc.collect()
        self.temporary = tempfile.TemporaryDirectory(prefix="desktop-ui-test-", dir=core.APP_DIR.parent)
        self.base = Path(self.temporary.name)
        self.assertTrue(core.inside(self.base.resolve(), core.APP_DIR.parent.resolve()))
        self.patches = [
            patch.object(desktop, "disk_snapshot", return_value={"root": "C:\\", "used": 70, "total": 100, "free": 30}),
            patch.object(desktop.cdrive_system, "read_system_items", return_value=[]),
            patch.object(desktop.cdrive_system, "run_action"),
            patch.object(desktop.messagebox, "showerror"),
            patch.object(core, "DATA_DIR", self.base / "audit"),
            patch.object(preferences, "read_autostart", return_value=False),
        ]
        self.disk, self.system_read, self.system_action, self.errors, *_ = [mock.start() for mock in self.patches]
        self.window = desktop.DesktopApplication()
        self.window.geometry("1160x760+20+20")
        self.window.attributes("-topmost", True)
        self.window.update()

    def tearDown(self):
        # No test may leave a worker holding the workspace fixture directory.
        self.window.cancel.set()
        self.pump(lambda: not self.window.busy)
        for timer in self.window.tk.call("after", "info"):
            self.window.tk.call("after", "cancel", timer)
        self.window.destroy()
        self.system_action.assert_not_called()
        self.errors.assert_not_called()
        for mock in reversed(self.patches):
            mock.stop()
        self.temporary.cleanup()
        gc.collect()
        if self.gc_enabled:
            gc.enable()

    def pump(self, condition, seconds=10):
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            self.window.update()
            time.sleep(.01)
        self.window.update()
        self.assertTrue(condition(), "Desktop condition timed out")

    def fixture_rules(self, qq_data=(b"qq1", b"qq222"), log_data=(b"log1234",)):
        # Use real recovered manifest names so the actual CleanupSession admits
        # both categories, rather than testing with a replacement session/model.
        files = {}
        for key, parts in (("qq", qq_data), ("logs", log_data)):
            root = self.base / key
            root.mkdir(exist_ok=True)
            files[key] = []
            for index, data in enumerate(parts):
                file = root / f"private-marker-{key}-{index}.tmp"
                file.write_bytes(data)
                files[key].append(file)
        rules = [core.Rule("应用程序 / QQ", self.base / "qq", "应用程序", days=0),
                 core.Rule("临时文件 / Windows日志", self.base / "logs", "临时文件", days=0)]
        return rules, files

    def scan(self, rules):
        self.window.navigate("cdrive")
        page = self.window.cdrive_page
        page.days.set("0")
        with patch.object(desktop.cdrive, "build_rules", return_value=rules) as build:
            page.scan_button.invoke()
            self.pump(lambda: not self.window.busy)
            build.assert_called_once_with(days=0)
        return page

    def category(self, page, name):
        return next(category for category in page.session.categories if category.name == name)

    @staticmethod
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from DesktopWorkflowTests.descendants(child)

    def test_all_selection_surfaces_share_green_check_assets_and_remove_account_captions(self):
        images = check_images(self.window)
        self.assertEqual(images["on"].get(0, 0), (70, 214, 160))
        self.assertEqual(images["on"].get(4, 9), (255, 255, 255))
        presets = [widget for widget in self.descendants(self.window.cdrive_page) if isinstance(widget, tk.Checkbutton)]
        self.assertTrue(presets)
        self.assertTrue(all(isinstance(widget, Checkbutton) for widget in presets))
        for widget in presets:
            self.assertEqual(str(widget.cget("selectimage")), str(images["on"]))
        rules, fixture_files = self.fixture_rules()
        page = self.scan(rules)
        card = next(card for card in page.cards.values() if card.category.selectable)
        self.assertEqual(card.itemcget("check", "image"), str(images["on" if card.category.selected else "off"]))
        card.toggle()
        self.assertEqual(card.itemcget("check", "image"), str(images["on" if card.category.selected else "off"]))
        self.window.navigate("settings")
        settings = self.window.views["settings"]
        boxes = [widget for widget in self.descendants(settings) if isinstance(widget, tk.Checkbutton)]
        self.assertEqual(len(boxes), 3)
        boxes[0].invoke()
        self.assertTrue(settings.autostart.get(), "The custom indicator must retain native variable behavior")
        for widget in boxes:
            self.assertEqual(str(widget.cget("selectimage")), str(images["on"]))
        self.window.navigate("duplicates")
        duplicate = self.window.views["duplicates"]
        duplicate.append_items([core.file_record(fixture_files["qq"][0], self.base / "qq", "重复文件")])
        duplicate.set_check("0", True)
        self.assertEqual(tuple(duplicate.tree.item("0", "image")), (str(images["on"]),))
        duplicate.set_check("0", False)
        self.assertEqual(tuple(duplicate.tree.item("0", "image")), (str(images["off"]),))
        self.window.navigate("large")
        self.assertIs(self.window.views["large"].check_images, images)
        captions = [self.window.status.get()]
        captions += [str(widget.cget("text")) for widget in self.descendants(self.window) if "text" in widget.keys()]
        for caption in captions:
            self.assertFalse(any(term in caption for term in ("本地工具", "无需账号", "无需登录")), caption)

    def test_default_home_sidebar_navigates_to_idle_cdrive(self):
        window = self.window
        self.assertEqual(window.current_page, "home")
        self.assertEqual(window.notebook.select(), str(window.home_page))
        self.assertIsNone(window.cdrive_page.session)
        window.nav_widgets["cdrive"][2].event_generate("<Button-1>")
        window.update()
        self.assertEqual(window.current_page, "cdrive")
        self.assertEqual(window.notebook.select(), str(window.cdrive_page))
        self.assertTrue(window.cdrive_page.idle.winfo_ismapped())
        self.assertTrue(window.cdrive_page.scan_button.winfo_ismapped())
        self.assertFalse(window.cdrive_page.results.winfo_ismapped())
        self.assertEqual(window.cdrive_page.days.get(), "0")
        self.assertEqual(window.cdrive_page.age_combo.get(), "全部时间")
        self.assertFalse(window.busy)
        self.system_read.assert_not_called()

    def test_short_cleanup_results_do_not_drift_on_mousewheel(self):
        rules, _ = self.fixture_rules()
        page = self.scan(rules)
        page.collapse_group('应用程序')
        self.window.update()
        self.assertLess(page.inner.winfo_reqheight(), page.canvas.winfo_height())
        page.canvas.yview_moveto(0)
        before = page.canvas.canvasy(0)
        for delta in (120, 120, -120, -120):
            page.canvas.event_generate('<MouseWheel>', delta=delta, x=20, y=20)
            self.window.update()
            self.assertEqual(page.canvas.canvasy(0), before, 'A short list must remain at the top')
        spacer = tk.Frame(page.inner, height=1000)
        spacer.pack()
        self.window.update()
        page.canvas.event_generate('<MouseWheel>', delta=-120, x=20, y=20)
        self.window.update()
        self.assertGreater(page.canvas.canvasy(0), before, 'Long results must still scroll')
        spacer.destroy()

    def test_sidebar_cleanup_total_is_independent_of_selection_and_refreshes_after_delete(self):
        rules, _ = self.fixture_rules()
        page = self.scan(rules)
        self.assertEqual(self.window.nav_metrics["cdrive"]["value"], 15)
        page.select_all(False)
        self.assertEqual(self.window.nav_badges["cdrive"].cget("text"), "15B")
        qq = self.category(page, "QQ")
        page.cards[qq.key].toggle()
        page.clean_button.invoke()
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.window.nav_metrics["cdrive"]["value"], 7)
        self.window.navigate("home")
        self.assertEqual(self.window.nav_badges["cdrive"].cget("text"), "7B")

    def test_duplicate_and_compression_scan_badges_use_correct_totals(self):
        folder = self.base / "scannable"
        folder.mkdir()
        data = b"duplicate" * (1024 * 128)
        for name in ("a.bin", "b.bin"):
            (folder / name).write_bytes(data)
        (folder / "small.txt").write_bytes(b"small")
        self.window.navigate("duplicates")
        page = self.window.views["duplicates"]
        page.folder.set(str(folder))
        page.scan_button.invoke()
        self.pump(lambda: not self.window.busy)
        self.assertEqual(page.clean_button.caption, "删除")
        self.assertFalse(self.window.status_label.winfo_ismapped())
        captions = [str(widget.cget("text")) for widget in self.descendants(page) if "text" in widget.keys()]
        self.assertFalse(any("只处理已勾选文件" in caption for caption in captions))
        self.assertEqual(self.window.nav_metrics["duplicates"]["value"], len(data), "One copy must remain")
        page.smart_select()
        self.assertEqual(self.window.nav_metrics["duplicates"]["value"], len(data))
        from cplus_cleaner.features.app_compression import software_compression as compression
        sample = compression.Software('sample','Sample app',str(folder),2*len(data)+5)
        with patch.object(compression,"scan",return_value=dict(items=[sample],errors=[],cancelled=False)) as scan, \
                patch.object(compression,"compress") as compact:
            self.window.navigate("compress")
            page = self.window.views["compress"]
            scan.assert_not_called()
            page.scan_button.invoke()
            self.pump(lambda: page._loaded and not self.window.busy)
            compact.assert_not_called()
        self.assertTrue(self.window.status_label.winfo_ismapped())
        self.assertEqual(self.window.nav_metrics["compress"]["value"], 2 * len(data) + 5)

    def test_uninstall_badge_matches_visible_apps_and_refreshes_to_zero(self):
        from cplus_cleaner.features.uninstall import software_uninstall as uninstall_backend
        self.window.navigate("uninstall")
        page = self.window.views["uninstall"]
        apps = [uninstall_backend.Software('visible','Visible app',1024,'20261023',str(self.base),'never-run')]
        with patch.object(uninstall_backend, "scan", return_value=dict(items=apps,errors=[])), patch.object(uninstall_backend, "uninstall") as uninstall:
            page.load()
            self.pump(lambda: not self.window.busy)
            self.assertEqual(len(page.rows), 1)
            self.assertEqual(self.window.nav_badges["uninstall"].cget("text"), "1款")
            uninstall.assert_not_called()
        with patch.object(uninstall_backend, "scan", return_value=dict(items=[],errors=[])):
            page.load()
            self.pump(lambda: not self.window.busy)
        self.assertEqual(self.window.nav_badges["uninstall"].cget("text"), "0款")


    def test_sidebar_badges_remain_visible_and_clickable_at_minimum_height(self):
        self.window.geometry("980x650+20+20")
        for key in self.window.nav_badges:
            if key != "home":
                self.window.set_nav_metric(key, 34 * 1024**3)
        self.window.set_nav_metric("uninstall", 53, "款")
        self.window.set_nav_metric("popup", 18, "项")
        self.window.update()
        for key, badge in self.window.nav_badges.items():
            if key == "home":
                self.assertFalse(badge.winfo_ismapped())
                continue
            x = badge.winfo_rootx() + badge.winfo_width() // 2
            y = badge.winfo_rooty() + badge.winfo_height() // 2
            self.assertEqual(self.window.winfo_containing(x, y), badge, key)
            label = self.window.nav_widgets[key][2]
            self.assertLessEqual(abs(y - (label.winfo_rooty() + label.winfo_height() // 2)), 1, key)
            from tkinter import font
            self.assertGreaterEqual(label.winfo_width(), font.Font(font=label.cget("font")).measure(label.cget("text")), key)
        self.window.nav_badges["cdrive"].event_generate("<Button-1>")
        self.window.update()
        self.assertEqual(self.window.current_page, "cdrive")
        self.assertEqual(self.window.nav_badges["uninstall"].cget("text"), "53款")

    def test_three_line_menu_and_operations_window_preserves_current_tool(self):
        labels = [label for label, _ in self.window.more_menu.entries]
        self.assertEqual(labels[:2], ["设置", "问题反馈"])
        self.assertIn(preferences.VERSION, labels[2])
        self.assertEqual(len(self.window.menu_button.find_all()), 3)
        preferences.append_activity("scheduled_cleanup", ok=False, error="test-failure")
        preferences.append_activity("scheduled_cleanup", ok=True, summary="释放 1KB")
        self.window.navigate("cdrive")
        tabs=self.window.notebook.tabs()
        dialog=self.window.open_logs()
        page = self.window.views["operations"]
        self.assertIsInstance(dialog,tk.Toplevel)
        self.assertIs(page.winfo_toplevel(),dialog)
        self.assertEqual(self.window.notebook.select(), str(self.window.cdrive_page))
        self.assertEqual(self.window.current_page,"cdrive")
        self.assertEqual(self.window.notebook.tabs(),tabs)
        self.assertEqual(len(page.tree.get_children()), 2)
        for _ in range(3):
            self.assertIs(self.window.open_logs(),dialog)
        preferences.append_activity('scheduled_cleanup',ok=True,summary='new record')
        self.assertIs(self.window.open_logs(),dialog)
        self.assertEqual(len(page.tree.get_children()),3)
        page.filter.set("失败")
        page.render()
        self.assertEqual(len(page.tree.get_children()), 1)
        page.tree.selection_set("0")
        self.window.update()
        self.assertIn("test-failure", page.tree.item("0", "values")[-1])
        self.assertFalse(any(isinstance(widget, tk.Text) for widget in self.descendants(page)))
        captions = [str(widget.cget("text")) for widget in self.descendants(page) if "text" in widget.keys()]
        self.assertFalse(any("查看详情" in caption or "刷新记录" in caption for caption in captions))
        self.assertNotIn('取消任务',captions)
        page.return_button.invoke()
        self.window.update()
        self.assertIsNone(self.window._operations_window)
        self.assertNotIn('operations',self.window.views)
        self.assertEqual(self.window.current_page,'cdrive')
        self.assertTrue(self.window.footer.winfo_ismapped())
        reopened=self.window.open_logs();self.window.update()
        self.assertIsNot(reopened,dialog)
        reopened.focus_force();reopened.event_generate('<Escape>');self.window.update()
        self.assertIsNone(self.window._operations_window)
        self.assertEqual(self.window.notebook.tabs(),tabs)

    def test_header_dropdown_dismisses_and_navigates_without_duplicate_windows(self):
        menu = self.window.more_menu
        self.window.open_menu()
        self.window.update()
        self.assertTrue(menu.visible)
        popup = menu.window
        menu.show()
        self.assertIs(menu.window, popup)
        self.assertIs(self.window.grab_current(), popup)
        self.assertIs(self.window.winfo_containing(popup.winfo_rootx()+30, popup.winfo_rooty()+25), menu.canvas)
        self.window.open_menu()
        self.window.update()
        self.assertFalse(menu.visible)
        self.assertIsNone(self.window.grab_current())
        menu.show(keyboard=True)
        self.window.update()
        menu._step(1)
        self.assertEqual(menu.active, 1)
        menu._step(1)
        self.assertEqual(menu.active, 3)  # Informational version skips keyboard selection.
        menu.invoke(2)
        self.assertTrue(menu.visible)
        menu.canvas.event_generate("<Button-1>", x=30, y=menu.TOP+14)
        self.window.update()
        self.assertEqual(self.window.current_page, "home")
        self.assertTrue(self.window._settings_window.winfo_exists())
        self.assertFalse(menu.visible)
        menu.show()
        self.window.update()
        menu.window.event_generate("<Escape>")
        self.window.update()
        self.assertFalse(menu.visible)
        menu.show()
        self.window.update()
        menu.canvas.event_generate("<Button-1>", x=-20, y=80)
        self.window.update()
        self.assertFalse(menu.visible)
        self.assertIsNone(self.window.grab_current())
        menu.show()
        self.window.update()
        self.window.geometry("980x650+20+20")
        self.window.update()
        self.assertFalse(menu.visible)

    def test_operations_delete_filtered_selection_preserves_new_records_and_formats_software_result(self):
        preferences.append_activity("software_restore", ok=True, summary="鲁大师：success", count=784,
                                    bytes_before=2984213504, bytes_after=2984214233)
        preferences.append_activity("scheduled_cleanup", ok=False, error="test-failure")
        self.window.open_logs()
        self.window.update()
        page = self.window.views["operations"]
        content = next(page.tree.item(index, "values")[-1] for index in page.tree.get_children()
                       if page.tree.item(index, "values")[1] == "压缩还原")
        self.assertIn("鲁大师：已完成", content)
        self.assertIn("784 个文件", content)
        self.assertNotIn("success", content)
        self.assertNotIn("{", content)
        page.filter.set("失败")
        page.render()
        page.tree.selection_set("0")
        self.window.update()
        self.assertEqual(page.delete_button.cget("text"), "删除")
        self.window.busy = True
        try:
            page.delete_button.invoke()
            self.assertEqual(len(preferences.read_operations()[0]), 2)
        finally:
            self.window.busy = False
        preferences.append_activity("settings", ok=True, summary="new record")
        page.delete_button.invoke()
        self.assertEqual({row["action"] for row in preferences.read_operations()[0]}, {"software_restore", "settings"})
        self.assertEqual(len(page.tree.get_children()), 0)
        self.assertIn("已删除 1 条", page.summary.get())
        self.window.navigate("home")
        self.window.open_logs()
        page.filter.set("全部")
        page.render()
        self.assertEqual(len(page.tree.get_children()), 2)
        page.tree.selection_set(page.tree.get_children())
        self.window.update()
        page.delete_button.invoke()
        self.assertEqual(preferences.read_operations()[0], [])
        self.assertEqual(len(page.tree.get_children()), 0)
        self.assertIn("disabled", page.delete_button.state())

    def test_operations_clear_all_ignores_filter_and_selection(self):
        self.window.geometry("980x650+20+20")
        preferences.append_activity("software_restore", ok=True, summary="Example：success")
        preferences.append_activity("scheduled_cleanup", ok=False, error="test-failure")
        for filename in ("operations.jsonl", "system-actions.jsonl"):
            (core.DATA_DIR / filename).write_text('{"action":"delete","ok":true}\n', encoding="utf-8")
        self.window.open_logs()
        page = self.window.views["operations"]
        page.filter.set("失败")
        page.render()
        self.window.update()
        self.assertEqual(len(page.tree.get_children()), 1)
        self.assertFalse(page.tree.selection())
        self.assertIn("disabled", page.delete_button.state())
        self.assertEqual(page.clear_button.cget("text"), "全部清空")
        self.assertLessEqual(page.delete_button.winfo_rootx()+page.delete_button.winfo_width(), page.clear_button.winfo_rootx())
        x = page.clear_button.winfo_rootx()+page.clear_button.winfo_width()//2
        y = page.clear_button.winfo_rooty()+page.clear_button.winfo_height()//2
        self.assertIs(self.window.winfo_containing(x, y), page.clear_button)
        self.window.busy = True
        try:
            page.clear_button.invoke()
            self.assertEqual(len(preferences.read_operations()[0]), 4)
        finally:
            self.window.busy = False
        preferences.append_activity("settings", ok=True, summary="new record")
        page.clear_button.invoke()
        self.assertEqual(preferences.read_operations(), ([], []))
        self.assertEqual(len(page.tree.get_children()), 0)
        self.assertIn("已全部清空", page.summary.get())
        self.window.navigate("home")
        self.window.open_logs()
        self.assertEqual(len(page.tree.get_children()), 0)

    def test_settings_save_persists_and_close_hides_then_tray_restores(self):
        self.window.navigate("settings")
        page = self.window.views["settings"]
        self.assertFalse(page.autostart.get())
        self.assertFalse(page.scheduled.get())
        self.assertFalse(page.tray.get())
        page.cleanup_time.set("25:00")
        page.save_button.invoke()
        self.assertIn("设置未保存", page.message.get())
        self.assertFalse((core.DATA_DIR / "settings.json").exists())
        page.cleanup_time.set("23:59")
        page.scheduled.set(True)
        page.autostart.set(True)
        page.tray.set(True)
        fake_tray = type("FakeTray", (), {"hwnd": 123, "stop": lambda self: None})()
        with patch.object(desktop.tray, "NativeTray") as constructor, \
                patch.object(preferences, "read_autostart", return_value=False), \
                patch.object(preferences, "set_autostart") as registry:
            constructor.return_value = fake_tray
            fake_tray.start = lambda: True
            page.save_button.invoke()
            registry.assert_called_once_with(True)
            self.assertTrue(preferences.load()["scheduled_cleanup"])
            self.assertTrue(self.window.scheduler.next_run)
            self.window.close()
            self.window.update()
            self.assertEqual(self.window.state(), "withdrawn")
            self.window.tray_events.put(("tray", "restore"))
            self.window.poll()
            self.window.update()
            self.assertEqual(self.window.state(), "normal")
        self.assertIn("设置已保存", page.message.get())

    def test_settings_and_feedback_are_single_windows_without_replacing_current_page(self):
        self.window.navigate('cdrive');self.window.update()
        original=self.window.notebook.select()
        tabs=self.window.notebook.tabs()
        previous=self.window.previous_tool
        for key,index in (('settings',0),('feedback',1)):
            self.window.open_menu();self.window.update()
            self.window.more_menu.invoke(index);self.window.update()
            dialog=getattr(self.window,f'_{key}_window')
            panel=self.window.views[key]
            self.assertIsInstance(dialog,tk.Toplevel)
            self.assertIs(panel.winfo_toplevel(),dialog)
            self.assertEqual(str(dialog.transient()),str(self.window))
            self.assertEqual(self.window.current_page,'cdrive')
            self.assertEqual(self.window.notebook.select(),original)
            self.assertEqual(self.window.notebook.tabs(),tabs)
            self.assertEqual(self.window.previous_tool,previous)
            if key=='settings':panel.cleanup_time.set('19:35')
            for _ in range(3):
                self.assertIs(self.window.navigate(key),dialog)
            if key=='settings':self.assertEqual(panel.cleanup_time.get(),'19:35')
            self.assertIsNone(self.window.grab_current())
            self.assertEqual(panel.return_button.caption if isinstance(panel.return_button,desktop.PillButton)
                             else panel.return_button.cget('text'),'关闭')
            panel.return_button.invoke();self.window.update()
            self.assertIsNone(getattr(self.window,f'_{key}_window'))
            self.assertNotIn(key,self.window.views)
            reopened=self.window.navigate(key);self.window.update()
            self.assertIsNot(reopened,dialog)
            reopened.focus_force();reopened.event_generate('<Escape>');self.window.update()
            self.assertIsNone(getattr(self.window,f'_{key}_window'))
            self.assertEqual(self.window.current_page,'cdrive')
            self.assertEqual(self.window.notebook.tabs(),tabs)
        self.assertFalse((core.DATA_DIR/'settings.json').exists())
        self.assertFalse((core.DATA_DIR/'feedback').exists())

    def test_tray_settings_opens_dialog_without_switching_main_page(self):
        self.window.navigate('cdrive');self.window.update()
        self.window.tray_events.put(('tray','settings'))
        self.window.poll();self.window.update()
        self.assertEqual(self.window.current_page,'cdrive')
        self.assertIsNotNone(self.window._settings_window)
        self.assertTrue(self.window._settings_window.winfo_ismapped())

    @patch.dict('cplus_cleaner.application.feedback.FEEDBACK_URLS', bug='', feature='', issues='')
    def test_feedback_panel_empty_links_and_environment_copy(self):
        from cplus_cleaner.application import feedback
        self.window.geometry("980x650+20+20")
        self.window.navigate("feedback")
        page = self.window.views["feedback"]
        self.window.update()
        self.assertFalse(self.window.footer.winfo_ismapped())
        labels = [widget.cget("text") for widget in self.descendants(page) if isinstance(widget, tk.Label)]
        self.assertIn(f"CPlus Cleaner v{preferences.VERSION}", labels)
        self.assertFalse(any(isinstance(widget, tk.Text) for widget in self.descendants(page)))
        with patch('cplus_cleaner.ui.desktop_panels.webbrowser.open_new_tab') as browser:
            for key, button in page.link_buttons.items():
                self.assertEqual(feedback.FEEDBACK_URLS[key], "")
                self.assertFalse(button.enabled)
                self.assert_on_page(button, page)
                button.invoke()
                page.open_link(key)
            browser.assert_not_called()
        self.assertIn("链接待配置", page.message.get())
        self.assert_on_page(page.copy_button, page)
        self.assertIn(f"CPlus Cleaner v{preferences.VERSION}", page.environment.get())
        self.assertIn("操作系统：Windows", page.environment.get())
        self.assertIn("系统架构：", page.environment.get())
        self.assertNotIn(str(Path.home()), page.environment.get())
        with patch.object(page, "clipboard_clear"), patch.object(page, "clipboard_append") as copy:
            page.copy_button.invoke()
            copy.assert_called_once_with(page.environment.get())
        self.assertFalse((core.DATA_DIR / "feedback").exists())
        self.window.navigate("home")
        self.window.navigate("feedback")
        self.assertIs(self.window.views["feedback"], page)

    def test_feedback_buttons_open_repository_issue_forms_in_both_languages(self):
        targets = {
            'bug': ('https://github.com/jackqiuai/cplus-cleaner/issues/new?template=bug_report.yml', 'Report a bug'),
            'feature': ('https://github.com/jackqiuai/cplus-cleaner/issues/new?template=feature_request.yml', 'Suggest a feature'),
            'issues': ('https://github.com/jackqiuai/cplus-cleaner/issues', 'View existing issues'),
        }
        self.window.navigate('feedback')
        page = self.window.views['feedback']
        self.assertEqual(page.message.get(), '')
        previous_language = desktop.i18n.language()
        try:
            for language in ('zh', 'en'):
                desktop.i18n.set_language(language)
                self.window.update()
                with patch('cplus_cleaner.ui.desktop_panels.webbrowser.open_new_tab', return_value=True) as browser:
                    for key, (url, english_caption) in targets.items():
                        button = page.link_buttons[key]
                        self.assertTrue(button.enabled)
                        button.invoke()
                        browser.assert_called_once_with(url)
                        browser.reset_mock()
                        if language == 'en':
                            caption = next(button.itemcget(item, 'text') for item in button.find_all()
                                           if button.type(item) == 'text')
                            self.assertEqual(caption, english_caption)
                            self.assertNotRegex(desktop.i18n.tr(page.message.get()), desktop.i18n.HAN)
        finally:
            desktop.i18n.set_language(previous_language)
        self.assertFalse((core.DATA_DIR/'feedback').exists())

    def test_feedback_configured_link_uses_browser_and_reports_failure(self):
        from cplus_cleaner.application import feedback
        self.window.navigate("feedback")
        page = self.window.views["feedback"]
        with patch('cplus_cleaner.ui.desktop_panels.webbrowser.open_new_tab', return_value=True) as browser:
            page.open_link("bug")
            browser.assert_called_once_with(feedback.FEEDBACK_URLS["bug"])
            self.assertIn("已在浏览器打开", page.message.get())
            browser.return_value = False
            page.open_link("bug")
            self.assertIn("无法打开浏览器", page.message.get())
            browser.side_effect = OSError('Browser is unavailable')
            page.link_buttons['bug'].invoke()
            self.assertIn("无法打开浏览器", page.message.get())

    def test_scheduled_job_updates_logs_and_results_without_system_actions(self):
        self.window.settings["scheduled_cleanup"] = True
        rules, files = self.fixture_rules()
        with patch.object(desktop.cdrive, "build_rules", return_value=rules):
            self.window.run_scheduled_cleanup()
            self.pump(lambda: not self.window.busy)
        self.assertFalse(any(file.exists() for group in files.values() for file in group))
        self.assertTrue(preferences.load()["last_cleanup_date"])
        records, _ = preferences.read_operations()
        jobs = [record for record in records if record["action"] == "scheduled_cleanup"]
        self.assertEqual(len(jobs), 2)
        completed = [job for job in jobs if "result" in job]
        self.assertEqual(completed[0]["result"]["deleted"], 3)

    def test_settings_registry_failure_keeps_previous_preferences(self):
        original = dict(self.window.settings)
        with patch.object(self.window, "ensure_tray"), \
                patch.object(preferences, "read_autostart", return_value=False), \
                patch.object(preferences, "set_autostart", side_effect=OSError("拒绝访问")):
            with self.assertRaisesRegex(OSError, "拒绝访问"):
                self.window.apply_settings(dict(original, autostart=True))
        self.assertEqual(self.window.settings, original)
        self.assertFalse((core.DATA_DIR / "settings.json").exists())

    def test_settings_controls_fit_minimum_window_size(self):
        self.window.geometry("980x650+20+20")
        self.window.navigate("settings")
        self.window.update()
        page = self.window.views["settings"]
        self.assert_on_page(page.save_button, page)
        self.assert_on_page(page.time_entry, page)
        self.assertFalse(self.window.footer.winfo_ismapped())
        self.window.status.set("任务完成")
        self.window.update()
        self.assertFalse(self.window.footer.winfo_ismapped())
        self.window.navigate("cdrive")
        self.window.update()
        self.assertTrue(self.window.footer.winfo_ismapped())

    def test_settings_file_failure_rolls_back_autostart_registration(self):
        original = dict(self.window.settings)
        with patch.object(self.window, "ensure_tray"), \
                patch.object(preferences, "set_autostart") as registry, \
                patch.object(preferences, "save", side_effect=OSError("磁盘不可写")):
            with self.assertRaisesRegex(OSError, "磁盘不可写"):
                self.window.apply_settings(dict(original, autostart=True))
        self.assertEqual([call.args[0] for call in registry.call_args_list], [True, False])
        self.assertEqual(self.window.settings, original)

    def test_all_cleanup_pages_default_to_all_time_with_readable_range_choices(self):
        for key in ("cdrive",):
            self.window.navigate(key)
            page = self.window.views[key]
            self.assertEqual(page.days.get(), "0")
            self.assertEqual(page.age_combo.get(), "全部时间")
            self.assertEqual(tuple(page.age_combo.cget('values')), ('全部时间', '7天内', '30天内', '180天内'))
            for days in (7, 30, 180):
                page.range_label.set(f"{days}天内")
                page.age_combo.event_generate("<<ComboboxSelected>>")
                self.window.update()
                self.assertEqual(page.days.get(), str(days))
            page.range_label.set("全部时间")
            page.age_combo.event_generate("<<ComboboxSelected>>")
            self.window.update()
            self.assertEqual(page.days.get(), "0")

    def test_chat_cleanup_pages_show_disk_capacity_and_refresh_on_reentry(self):
        self.window.geometry("980x650+20+20")
        total = 180 * 1024**3
        with patch.object(core, "chat_rules") as chat_scan:
            for key in ("chat",):
                for used in (150 * 1024**3, 140 * 1024**3):
                    self.disk.return_value = dict(root="C:\\", total=total, used=used, free=total-used)
                    self.window.navigate("home")
                    self.window.navigate(key)
                    self.window.update()
                    page = self.window.views[key]
                    self.assertEqual(page.disk.amount.cget("text"), f"{desktop.size_text(used)} / {desktop.size_text(total)}")
                    self.assertEqual(page.disk.info["used"], used)
                    self.assertEqual(page.disk.info["total"], total)
                    self.assert_on_page(page.disk.amount, page)
                    self.assertGreaterEqual(page.disk.amount.winfo_width(), page.disk.amount.winfo_reqwidth())
                    self.assertTrue(page.scan_button.winfo_ismapped())
                    self.assertIsNone(page.session)
                    self.assertFalse(self.window.busy)
            chat_scan.assert_not_called()

    def test_original_category_and_navigation_icons_are_mapped_and_loadable(self):
        mapping = json.loads((desktop.ASSETS / "category-icons.json").read_text(encoding="utf-8"))
        self.assertEqual(len(mapping["categoriesByIndex"]), 91)
        self.assertEqual(mapping["missing"], [])
        self.assertEqual(mapping["system"]["virtual_memory"], "categories/40004.png")
        for category, path in mapping["categories"].items():
            group, name = category.split(" / ", 1)
            expected = self.window.images.get(path)
            self.assertIsNotNone(expected, category)
            self.assertIs(self.window.images.icon(name, group), expected)
            self.assertEqual((expected.width(), expected.height()), (48, 48))
        for key, _, _ in desktop.DesktopApplication.NAV:
            for active in (False, True):
                image = self.window.images.navigation(key, active)
                self.assertIsNotNone(image, (key, active))
                self.assertEqual((image.width(), image.height()), (20, 20))

    def test_virtual_memory_475gb_is_visible_first_and_only_opens_system_settings(self):
        rules, files = self.fixture_rules(qq_data=(), log_data=(b"small",))
        self.system_read.return_value = [dict(id="pagefile", name="系统虚拟内存", size=5_100_273_664,
                                             status="系统管理", actions=["pagefile_settings"])]
        page = self.scan(rules)
        self.window.update()
        card = page.virtual_memory_card
        self.assertIsNotNone(card)
        self.assertTrue(card.winfo_ismapped())
        self.assertIn("4.75GB", page.subtitle.get())
        texts = [card.itemcget(i, "text") for i in card.find_all() if card.type(i) == "text"]
        self.assertIn("系统虚拟内存", texts)
        self.assertIn("4.75GB", texts)
        self.assertLess(card.winfo_rooty(), next(iter(page.cards.values())).winfo_rooty())
        self.assertEqual(page.session.total_bytes, 5)
        page.select_all(True)
        self.assertEqual(page.session.selected_bytes, 5)
        with patch.object(page, "run_system_action") as system_action, patch.object(core, "delete_files") as delete:
            card.invoke()
            system_action.assert_called_once_with("pagefile_settings")
            delete.assert_not_called()

    def test_unknown_virtual_memory_size_remains_unknown(self):
        rules, _ = self.fixture_rules()
        self.system_read.return_value = [dict(id="pagefile", name="系统虚拟内存", size=None,
                                             status="权限不足", actions=[])]
        page = self.scan(rules)
        self.assertIn("虚拟内存占用 未知", page.subtitle.get())
        card = page.virtual_memory_card
        texts = [card.itemcget(i, "text") for i in card.find_all() if card.type(i) == "text"]
        self.assertIn("未知", texts)

    def test_system_actions_keep_results_inline_without_extra_windows(self):
        rules, _ = self.fixture_rules()
        self.system_read.return_value = [dict(id="pagefile", name="系统虚拟内存", size=123,
                                             actions=list(desktop.CategoryPage.SYSTEM_ACTIONS))]
        page = self.scan(rules)
        for action_id in desktop.CategoryPage.SYSTEM_ACTIONS:
            with self.subTest(action=action_id), \
                 patch.object(desktop.cdrive_system, "run_action", return_value=dict(
                     ok=True, message="系统操作已完成", command=["fixture.exe"], output="raw output")) as action, \
                 patch.object(desktop.messagebox, "askyesno", return_value=True):
                page.run_system_action(action_id)
                self.pump(lambda: not self.window.busy)
                action.assert_called_once()
                self.assertEqual(action.call_args.args[0], action_id)
                self.assertEqual(page.note.get(), "系统操作已完成")
                self.assertFalse(any(isinstance(widget, tk.Toplevel) for widget in self.descendants(self.window)))
        with patch.object(desktop.cdrive_system, "run_action", return_value=dict(
                 ok=False, message="无法打开系统设置：权限不足")):
            page.virtual_memory_card.invoke()
            self.pump(lambda: not self.window.busy)
            self.assertIn("权限不足", page.note.get())

    def test_privacy_uses_type_cards_without_system_operations(self):
        from cplus_cleaner.features.privacy import privacy
        from cplus_cleaner.features.privacy import privacy_ui
        session = privacy.Session()
        session.scanned = True
        session.categories['chrome_history'].records = [dict(id='one'), dict(id='two')]
        self.window.navigate("privacy")
        page = self.window.views["privacy"]
        with patch.object(privacy, "scan", return_value=session):
            page.scan_button.invoke()
            self.pump(lambda: not self.window.busy)
        self.assertIsInstance(page, privacy_ui.PrivacyPage)
        self.assertEqual(page.session.total_count, 2)
        self.assertEqual(page.session.selected_count, 2)
        self.assertEqual(len(page.cards), 13)
        self.assertEqual(self.window.nav_metrics['privacy']['text'], '2条')
        self.assertTrue(all(card.art for card in page.cards.values()))
        page.cards['chrome_history'].click()
        self.assertEqual(page.session.selected_count, 0)
        self.assertFalse(page.clean_button.enabled)
        self.assertFalse(any(isinstance(w, ttk.Treeview) for w in self.descendants(page)))
        self.assertFalse(hasattr(page, "system_panel"))
        self.system_read.assert_not_called()

    def test_privacy_clean_updates_count_and_records_summary_without_private_metadata(self):
        from cplus_cleaner.features.privacy import privacy
        session = privacy.Session()
        session.scanned = True
        session.categories['chrome_history'].records = [dict(id='sample', kind='browser', url='private history')]
        self.window.navigate('privacy')
        page = self.window.views['privacy']
        with patch.object(privacy, 'scan', return_value=session):
            page.scan_button.invoke()
            self.pump(lambda: not self.window.busy)
        def clean(*args):
            session.categories['chrome_history'].records.clear()
            return dict(cleaned_count=1, errors=[])
        with patch.object(privacy, 'clean', side_effect=clean) as action:
            page.clean_button.invoke()
            self.pump(lambda: not self.window.busy)
        action.assert_called_once()
        self.assertEqual(self.window.nav_metrics['privacy']['text'], '0条')
        self.assertIn('已清理1条', page.status.get())
        self.assertFalse(page.clean_button.enabled)
        data = (core.DATA_DIR/'activity.jsonl').read_text('utf-8')
        self.assertIn('privacy_cleanup', data)
        self.assertNotIn('private history', data)

    def test_privacy_group_toggle_shared_checks_without_diagnostics_entry(self):
        from cplus_cleaner.features.privacy import privacy
        self.window.navigate('privacy')
        page = self.window.views['privacy']
        self.assertIs(page.checks, check_images(self.window))
        page.session.scanned = True
        page.session.categories['app_switched'].records = [dict(id='x')]
        page.update_totals()
        page.groups['windows']['toggle'].invoke()
        self.assertEqual(page.session.selected_count, 0)
        self.assertEqual(page.groups['windows']['toggle'].cget('image'), str(page.checks['off']))
        page.groups['windows']['toggle'].invoke()
        self.assertEqual(page.session.selected_count, 1)
        page.collapse('windows')
        self.assertTrue(page.groups['windows']['collapsed'])
        page.collapse('windows')
        self.assertFalse(hasattr(page, 'details_button'))
        self.assertFalse(any(isinstance(w, tk.Toplevel) for w in page.winfo_children()))
        self.window.geometry('980x650')
        self.window.update()
        for card in page.cards.values():
            self.assertLessEqual(card.winfo_x()+card.winfo_width(), page.inner.winfo_width())

    def test_chat_attachments_are_grouped_by_content_type_across_accounts(self):
        for account, size in (("account-a", 3), ("account-b", 5)):
            folder = self.base / account / "FileStorage/Image"
            folder.mkdir(parents=True)
            (folder / "private-photo.jpg").write_bytes(b"x" * size)
        from cplus_cleaner.features.chat import chat_cleanup
        self.window.navigate("chat")
        page = self.window.views["chat"]
        chat_cleanup.save_path('wechat', self.base)
        bases = {key: self.base/'isolated'/key for key in ('documents', 'roaming', 'local')}
        with patch.object(chat_cleanup, 'locations', return_value=bases), patch.object(chat_cleanup, 'installed_records', return_value=([], [])):
            page.scan_button.invoke()
            self.pump(lambda: not self.window.busy)
        self.assertEqual(page.session.total_bytes, 8)
        self.assertEqual(len(page.cards), 1)
        self.assertEqual(page.session.categories[0].name, "聊天图片")
        self.assertEqual(page.session.categories[0].file_count, 2)
        self.assertFalse(any(isinstance(w, ttk.Treeview) for w in self.descendants(page)))
        self.system_read.assert_not_called()

    def test_scan_button_aggregates_real_files_into_cards_without_file_details(self):
        rules, files = self.fixture_rules()
        page = self.scan(rules)
        self.assertEqual(page.session.total_files, 3)
        self.assertEqual(page.session.total_bytes, 15)
        self.assertEqual(self.category(page, "QQ").file_count, 2)
        self.assertEqual(self.category(page, "QQ").bytes_count, 8)
        self.assertEqual(self.category(page, "Windows日志").bytes_count, 7)
        self.assertEqual({i.path for i in page.session.selected_items()}, {str(p) for p in [*files['qq'], *files['logs']]})
        self.assertEqual(page.session.selected_bytes, 15)
        self.assertTrue(page.clean_button.enabled)
        self.assertEqual(len(page.cards), 2)
        self.assertTrue(page.results.winfo_ismapped())
        self.assertFalse(page.idle.winfo_ismapped())
        self.assertIn(desktop.size_text(15), page.heading.get())
        self.assertEqual(self.system_read.call_count, 1)
        self.assertFalse(any(isinstance(widget, ttk.Treeview) for widget in self.descendants(page.results)))
        for card in page.cards.values():
            painted = "\n".join(card.itemcget(item, "text") for item in card.find_all() if card.type(item) == "text")
            self.assertIn(card.category.name, painted)
            self.assertNotIn(str(self.base), painted)
            for file in [*files["qq"], *files["logs"]]:
                self.assertNotIn(file.name, painted)
                self.assertTrue(file.exists(), "Scanning must not remove the fixture")

    def test_select_type_and_clean_deletes_all_of_that_type_only_then_refreshes(self):
        rules, files = self.fixture_rules()
        page = self.scan(rules)
        qq = self.category(page, "QQ")
        page.select_all(False)
        page.cards[qq.key].toggle()
        self.assertEqual({item.path for item in page.session.selected_items()}, {str(file) for file in files["qq"]})
        self.assertEqual(page.session.selected_bytes, 8)
        self.assertTrue(page.clean_button.enabled)
        with patch.object(core, "delete_files", wraps=core.delete_files) as delete:
            page.clean_button.invoke()
            self.pump(lambda: not self.window.busy)
            self.assertEqual(delete.call_count, 1)
            candidates = delete.call_args.args[0]
            self.assertEqual(len(candidates), 2)
            for candidate in candidates:
                self.assertTrue(core.inside(Path(candidate.path).resolve(), self.base.resolve()))
        self.assertTrue(all(not file.exists() for file in files["qq"]))
        self.assertTrue(all(file.exists() for file in files["logs"]))
        self.assertEqual(page.session.total_files, 1)
        self.assertEqual(page.session.total_bytes, 7)
        self.assertEqual(qq.file_count, 0)
        self.assertEqual(qq.bytes_count, 0)
        self.assertFalse(qq.selected)
        self.assertFalse(page.clean_button.enabled)
        self.assertIn(desktop.size_text(7), page.heading.get())
        self.assertIn("已清理", page.note.get())

    def test_real_streaming_and_cancel_keep_partial_category_totals(self):
        rules, files = self.fixture_rules(qq_data=tuple(b"abc" for _ in range(160)), log_data=())
        window, page = self.window, self.window.cdrive_page
        window.navigate("cdrive")
        page.days.set("0")
        release = threading.Event()
        original_walk = core.walk_files
        def paused_walk(root, cancel=None, *args, **kwargs):
            for index, file in enumerate(original_walk(root, cancel, *args, **kwargs)):
                yield file
                if index == 124:
                    release.wait(10)
                    if cancel and cancel.is_set():
                        return
        try:
            with patch.object(desktop.cdrive, "build_rules", return_value=rules), \
                 patch.object(core, "walk_files", side_effect=paused_walk):
                page.scan_button.invoke()
                self.pump(lambda: page.session is not None and page.session.total_files >= 100)
                self.assertTrue(window.busy, "A batch must reach the actual UI before the scanner completes")
                self.assertTrue(page.results.winfo_ismapped())
                self.assertTrue(page.scanning)
                qq = self.category(page, "QQ")
                self.assertTrue(qq.selected, "Default choices should be checked when their first batch arrives")
                page.cards[qq.key].toggle()
                self.assertTrue(qq.selected, "Card selection must not change during a scan")
                window.cancel.set()
                release.set()
                self.pump(lambda: not window.busy)
                self.assertGreaterEqual(page.session.total_files, 100)
                self.assertLess(page.session.total_files, 160)
                self.assertEqual(page.session.total_bytes, 3 * page.session.total_files)
                self.assertIn("取消", page.note.get())
                self.assertFalse(page.scanning)
                self.assertTrue(all(file.exists() for file in files["qq"]))
        finally:
            release.set()
            self.pump(lambda: not window.busy)

    def test_empty_type_cannot_be_selected_and_system_occupancy_is_not_reclaimable(self):
        rules, files = self.fixture_rules(qq_data=(), log_data=(b"small",))
        system = [{"id": "pagefile", "name": "分页文件", "path": r"C:\pagefile.sys", "size": 9_999_999,
                   "status": "Windows 管理", "action": "只打开设置", "actions": ["pagefile_settings"]},
                  {"id": "components", "name": "组件存储", "path": "", "size": None, "actions": []}]
        self.system_read.return_value = system
        page = self.scan(rules)
        empty = self.category(page, "QQ")
        page.cards[empty.key].toggle()
        self.assertFalse(empty.selected)
        self.assertFalse(empty.selectable)
        self.assertTrue(page.clean_button.enabled, "The nonempty log category is selected by default")
        page.select_all(True)
        self.assertFalse(empty.selected)
        self.assertEqual(page.session.selected_count, 1)
        self.assertEqual(page.session.selected_bytes, 5)
        self.assertEqual(page.session.total_bytes, 5)
        self.assertEqual(page.system_items, system)
        self.assertFalse(hasattr(page, "system_panel"))
        self.assertFalse(hasattr(page, "system_summary"))
        self.window.navigate('system')
        self.pump(lambda: not self.window.busy)
        system_page = self.window.views['system']
        self.assertEqual(system_page.rows['component_store'][0].cget('text'), '未知')
        self.assertEqual([item.path for item in page.session.selected_items()], [str(files["logs"][0])])

    def test_default_scan_options_are_checked_and_excluded_groups_are_not_scanned(self):
        page = self.window.cdrive_page
        self.assertEqual({name for name, var in page.scan_options.items() if var.get()},
                         {"缓存文件", "临时文件", "日志文件", "系统报告", "应用缓存"})
        self.assertFalse(page.scan_options["备份文件"].get())
        rules, files = self.fixture_rules()
        backup = self.base / "backup"
        backup.mkdir()
        saved = backup / "saved.bak"
        saved.write_bytes(b"backup")
        rules.append(core.Rule("备份文件 / Boot备份信息", backup, "备份文件", days=0))
        page.scan_options["应用缓存"].set(False)
        with patch.object(core, "walk_files", wraps=core.walk_files) as walk:
            page = self.scan(rules)
        self.assertEqual(page.session.total_bytes, 7)
        self.assertEqual([c.name for c in page.session.categories], ["Windows日志"])
        scanned = {call.args[0] for call in walk.call_args_list}
        self.assertNotIn(self.base / "qq", scanned)
        self.assertNotIn(backup, scanned)
        self.assertTrue(saved.exists())
        self.assertTrue(all(file.exists() for file in files["qq"]))

    def test_manual_uncheck_persists_and_restore_defaults_excludes_backups(self):
        rules, _ = self.fixture_rules()
        backup = self.base / "backup"
        backup.mkdir()
        (backup / "saved.bak").write_bytes(b"backup")
        rules.append(core.Rule("备份文件 / Boot备份信息", backup, "备份文件", days=0))
        page = self.window.cdrive_page
        page.scan_options["备份文件"].set(True)
        page = self.scan(rules)
        qq = self.category(page, "QQ")
        saved = self.category(page, "Boot备份信息")
        self.assertTrue(qq.selected)
        self.assertFalse(saved.selected, "Scanning a backup group does not auto-select it for deletion")
        page.cards[qq.key].toggle()
        self.window.update()
        page.update_totals()
        self.assertFalse(qq.selected)
        self.assertEqual(page.session.selected_bytes, 7)
        page.select_all(False)
        self.assertEqual(page.session.selected_count, 0)
        self.assertFalse(page.clean_button.enabled)
        page.restore_defaults()
        self.assertTrue(qq.selected)
        self.assertEqual(page.session.selected_bytes, 15)
        self.assertFalse(saved.selected)
        self.assertFalse(page.scan_options["备份文件"].get())

    def test_minimum_size_keeps_home_scan_and_cdrive_scan_clean_buttons_visible(self):
        window = self.window
        window.geometry("980x650+20+20")
        window.update()
        self.assertEqual((window.winfo_width(), window.winfo_height()), (980, 650))
        self.assert_on_page(window.home_page.scan_button, window.home_page)
        window.navigate("cdrive")
        window.update()
        self.assert_on_page(window.cdrive_page.scan_button, window.cdrive_page)
        rules, _ = self.fixture_rules()
        page = self.scan(rules)
        self.assert_on_page(page.clean_button, page)
        self.assert_on_page(page.rescan_button, page)
        for card in page.cards.values():
            self.assertTrue(card.winfo_ismapped())

    def assert_on_page(self, widget, page):
        self.assertTrue(widget.winfo_ismapped())
        self.assertGreater(widget.winfo_width(), 10)
        self.assertGreater(widget.winfo_height(), 10)
        left, top = widget.winfo_rootx(), widget.winfo_rooty()
        self.assertGreaterEqual(left, page.winfo_rootx())
        self.assertGreaterEqual(top, page.winfo_rooty())
        self.assertLessEqual(left + widget.winfo_width(), page.winfo_rootx() + page.winfo_width())
        self.assertLessEqual(top + widget.winfo_height(), page.winfo_rooty() + page.winfo_height())


if __name__ == "__main__":
    unittest.main()

# Purpose: Regression tests for main window, navigation, feature composition and settings dialog
