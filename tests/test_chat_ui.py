"""Manual entry, grouping and selected deletion using isolated chat fixtures."""
from contextlib import ExitStack
from dataclasses import replace
import gc
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from cplus_cleaner.features.chat import chat_cleanup
from cplus_cleaner.common import core
from cplus_cleaner.ui import desktop
from cplus_cleaner.features.home import home_results
from cplus_cleaner.application import preferences
from tests.test_chat_cleanup import fixtures


class ChatUITests(unittest.TestCase):
    def setUp(self):
        self.gc_enabled = gc.isenabled()
        gc.disable()
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix='chat-ui-', dir=core.APP_DIR.parent)
        self.root = Path(self.temp.name)
        self.bases, self.files = fixtures(self.root)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(core, 'DATA_DIR', self.root/'state'))
        self.stack.enter_context(patch.object(preferences, 'load', return_value=dict(preferences.DEFAULTS)))
        self.stack.enter_context(patch.object(preferences, 'read_autostart', return_value=False))
        self.stack.enter_context(patch.object(chat_cleanup, 'locations', return_value=self.bases))
        self.stack.enter_context(patch.object(chat_cleanup, 'installed_records', return_value=([], [])))
        self.stack.enter_context(patch.object(chat_cleanup.software_windows, 'running_images', return_value=[]))
        self.stack.enter_context(patch.object(desktop, 'disk_snapshot', return_value=dict(root='C:\\', total=1000, used=700, free=300)))
        self.errors = self.stack.enter_context(patch.object(desktop.messagebox, 'showerror'))
        self.window = desktop.DesktopApplication()
        self.window.geometry('980x650+20+20')
        self.window.attributes('-topmost', True)
        self.callbacks = []
        self.window.report_callback_exception = lambda kind, error, trace: self.callbacks.append(str(error))
        self.window.navigate('chat')
        self.window.update()
        self.page = self.window.views['chat']

    def tearDown(self):
        self.window.cancel.set()
        self.pump(lambda: not self.window.busy)
        for timer in self.window.tk.call('after', 'info'):
            self.window.tk.call('after', 'cancel', timer)
        self.window.destroy()
        self.errors.assert_not_called()
        self.assertEqual(self.callbacks, [])
        self.stack.close()
        self.temp.cleanup()
        gc.collect()
        if self.gc_enabled:
            gc.enable()

    def pump(self, condition):
        deadline = time.monotonic()+8
        self.window.update()
        while not condition() and time.monotonic() < deadline:
            self.window.update()
            time.sleep(.01)
        self.window.update()
        self.assertTrue(condition())

    def scan(self):
        self.page.scan_button.invoke()
        self.pump(lambda: not self.window.busy)

    def test_single_manual_entry_keeps_wechat_assets_and_idle_results(self):
        self.assertIn('chat', self.window.nav_widgets)
        self.assertNotIn('wechat', self.window.nav_widgets)
        self.assertNotIn('qq', self.window.nav_widgets)
        self.assertIs(self.window.nav_icons['chat'][1], self.window.images.get('nav-wechat.png'))
        self.assertIs(self.page.icons['wechat'], self.window.images.get('icon-wechat.png'))
        with patch.object(chat_cleanup, 'scan') as scan:
            self.window.navigate('home')
            self.window.home_page.open_feature('chat')
            self.window.update()
            self.window.navigate('chat')
            self.window.update()
            scan.assert_not_called()
        self.assertIsNone(self.page.session)
        self.assertFalse(hasattr(self.page, 'filter_box'))
        self.assertFalse(hasattr(self.page, 'age_combo'))
        self.assertNotIn('chat', self.window.nav_metrics)
        for button in (self.page.scan_button, self.page.clean_button, self.page.groups['wechat']['folder_button']):
            x, y = button.winfo_rootx()+button.winfo_width()//2, button.winfo_rooty()+button.winfo_height()//2
            self.assertIs(self.window.winfo_containing(x, y), button)

    def test_scan_selects_positive_categories_and_reentry_preserves_manual_uncheck(self):
        empty = self.bases['documents']/'WeChat Files/account/FileStorage/Temp/empty.dat'
        empty.parent.mkdir(parents=True)
        empty.touch()
        self.scan()
        self.assertEqual(set(self.page.groups), set(chat_cleanup.APPS))
        self.assertGreater(self.page.session.total_bytes, 0)
        self.assertEqual(self.page.session.total_bytes, self.page.session.selected_bytes)
        self.assertEqual(self.window.nav_metrics['chat']['value'], self.page.session.total_bytes)
        self.assertTrue(all(category.selected == (category.bytes_count > 0) for category in self.page.session.categories))
        image = next(card for card in self.page.cards.values() if card.category.name == '聊天图片' and card.app_id == 'wechat')
        image.toggle()
        self.assertFalse(image.category.selected)
        old = self.page.session
        self.window.navigate('home')
        self.window.navigate('chat')
        self.window.update()
        self.assertIs(self.page.session, old)
        self.assertFalse(image.category.selected)
        self.page.select_all(False)
        self.page.select_all_button.invoke()
        self.assertTrue(all(category.selected == (category.bytes_count > 0) for category in self.page.session.categories))
        self.assertFalse(any(isinstance(widget, ttk.Treeview) for widget in self.page.winfo_children()))

    def test_delete_only_selected_cache_syncs_overlapping_cdrive_snapshot(self):
        self.scan()
        self.page.select_all(False)
        for category in self.page.session.categories:
            self.page.session.set_selected(category.key, category.name == '缓存文件')
        self.page.update_totals()
        cache = next(item for item in self.page.snapshot['items'] if item.category == '微信 / 缓存文件')
        cdrive = self.window.cdrive_page
        rule = core.Rule('临时文件 / 临时文件', Path(cache.root), '临时文件')
        candidate = replace(cache, category=rule.name)
        manifest = [dict(index=0, name='临时文件', group='临时文件')]
        cdrive.prepare(([rule], [], manifest))
        cdrive.consume([candidate])
        cdrive.flush_paint()
        self.window.home_page.results['cdrive'] = dict(value=cache.size, payload=dict(items=[candidate]))
        self.page.clean_button.invoke()
        self.pump(lambda: not self.window.busy)
        self.assertFalse(self.files['wechat_cache'].exists())
        self.assertTrue(self.files['wechat_image'].exists())
        self.assertTrue(self.files['signal_attachment'].exists())
        self.assertTrue(self.files['signal_db'].exists())
        self.assertEqual(cdrive.session.total_bytes, 0)
        self.assertEqual(self.window.nav_metrics['cdrive']['value'], 0)
        self.assertEqual(self.window.home_page.results['cdrive']['value'], 0)
        self.assertEqual(self.window.nav_metrics['chat']['value'], self.page.session.total_bytes)
        records, _ = preferences.read_operations()
        self.assertTrue(any(record['action'] == 'chat_cleanup' for record in records))

    def test_home_snapshot_adoption_does_not_rescan_and_busy_task_reenables_button(self):
        payload = chat_cleanup.scan(icons=False)
        with patch.object(chat_cleanup, 'scan') as scanner:
            self.assertTrue(home_results.apply_result(self.window, 'chat', dict(payload=payload)))
            self.window.navigate('chat', use_existing=True)
            scanner.assert_not_called()
        release = threading.Event()
        self.addCleanup(release.set)
        self.window.start('工作区示例任务', lambda: release.wait(5), lambda result: None)
        self.page.update_totals()
        self.assertFalse(self.page.scan_button.enabled)
        release.set()
        self.pump(lambda: not self.window.busy)
        self.assertTrue(self.page.scan_button.enabled)


if __name__ == '__main__':
    unittest.main()
