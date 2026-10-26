"""System page workflows; Windows reads and every action are mocked."""
from contextlib import ExitStack
from pathlib import Path
import gc
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.ui import desktop
from cplus_cleaner.localization import i18n
from cplus_cleaner.application import preferences
from cplus_cleaner.features.system.system_management_ui import SystemManagementPage


def fixture_items():
    return [dict(id=key, name=title, size=None if key == 'component_store' else 1024,
                 status='系统管理', actions=list(actions))
            for key, title, actions in SystemManagementPage.ITEMS]


class SystemManagementTests(unittest.TestCase):
    def setUp(self):
        # Baseline captions must not depend on the runner's Windows language.
        language = patch.object(desktop.i18n, 'detect_language', return_value='zh')
        language.start()
        self.addCleanup(language.stop)
        self.gc_enabled = gc.isenabled()
        gc.disable()
        gc.collect()
        self.stack = ExitStack()
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(dir=core.APP_DIR.parent))
        self.stack.enter_context(patch.object(core, 'DATA_DIR', Path(self.temp)))
        self.stack.enter_context(patch.object(preferences, 'read_autostart', return_value=False))
        self.stack.enter_context(patch.object(i18n, 'detect_language', return_value='zh'))
        self.stack.enter_context(patch.object(desktop, 'disk_snapshot', return_value=dict(root='C:\\', total=100, used=70, free=30)))
        self.read = self.stack.enter_context(patch.object(desktop.cdrive_system, 'read_system_items', return_value=fixture_items()))
        self.action = self.stack.enter_context(patch.object(desktop.cdrive_system, 'run_action', return_value=dict(ok=True, message='系统操作已完成')))
        self.confirm = self.stack.enter_context(patch.object(desktop.messagebox, 'askyesno', return_value=True))
        self.delete = self.stack.enter_context(patch.object(core, 'delete_files', side_effect=AssertionError('No ordinary deletion')))
        self.app = desktop.DesktopApplication()
        self.app.geometry('980x650+20+20')
        self.errors = []
        self.app.report_callback_exception = lambda kind, error, trace: self.errors.append(str(error))

    def tearDown(self):
        self.pump(lambda: not self.app.busy)
        for timer in self.app.tk.call('after', 'info'):
            self.app.tk.call('after', 'cancel', timer)
        self.app.destroy()
        self.assertEqual(self.errors, [])
        self.delete.assert_not_called()
        self.stack.close()
        i18n.set_language('zh')
        gc.collect()
        if self.gc_enabled:
            gc.enable()

    def pump(self, condition):
        until = time.monotonic() + 8
        while not condition() and time.monotonic() < until:
            self.app.update()
            time.sleep(.01)
        self.app.update()
        self.assertTrue(condition())

    def open_page(self):
        self.app.navigate('system')
        self.pump(lambda: not self.app.busy)
        return self.app.views['system']

    def test_independent_page_after_popup_readonly_entry_and_cdrive_section_removed(self):
        keys = [key for key, _, _ in self.app.NAV]
        self.assertEqual(keys[keys.index('popup')+1], 'system')
        self.read.assert_not_called()
        self.app.cdrive_page.prepare(([], fixture_items(), []))
        self.assertFalse(hasattr(self.app.cdrive_page, 'system_panel'))
        self.assertIsNotNone(self.app.cdrive_page.virtual_memory_card)
        self.assertIn('1KB', self.app.cdrive_page.virtual_memory_amount.cget('text'))
        page = self.open_page()
        self.assertEqual(set(page.system_buttons), set(page.SYSTEM_ACTIONS))
        self.action.assert_not_called()
        self.assertEqual(page.rows['component_store'][0].cget('text'), '未知')
        self.assertTrue(page.refresh_button.enabled)
        self.assertFalse(self.app.footer.winfo_ismapped())
        nav = self.app.nav_widgets['system'][0]
        self.assertLessEqual(nav.winfo_rooty()+nav.winfo_height(), self.app.winfo_rooty()+self.app.winfo_height())
        icon_label, regular, active = self.app.nav_icons['system']
        above = self.app.nav_icons['cdrive'][0]
        self.assertEqual((regular.width(), regular.height()), (20, 20))
        self.assertEqual((active.width(), active.height()), (20, 20))
        self.assertEqual(icon_label.winfo_rootx()+icon_label.winfo_width()/2,
                         above.winfo_rootx()+above.winfo_width()/2)
        colors = {regular.get(x,y) for x in range(20) for y in range(20) if not regular.transparency_get(x,y)}
        self.assertEqual(colors, {(134,177,177)})
        self.app.navigate('home')
        self.app.navigate('system')
        self.pump(lambda: not self.app.busy)
        self.assertIs(self.app.views['system'], page)

    def test_actions_confirm_where_needed_and_refresh_without_result_windows(self):
        page = self.open_page()
        for action in page.SYSTEM_ACTIONS:
            self.action.reset_mock()
            self.confirm.reset_mock()
            page.system_buttons[action].invoke()
            self.pump(lambda: not self.app.busy)
            self.action.assert_called_once()
            self.assertEqual(self.action.call_args.args[0], action)
            if action in ('empty_recycle_bin', 'hibernate_off', 'hibernate_on', 'cleanup_component_store'):
                self.confirm.assert_called_once()
            else:
                self.confirm.assert_not_called()
            self.assertEqual(page.note.get(), '系统操作已完成')
            self.assertEqual([item['id'] for item in self.app.cdrive_page.system_items], [item['id'] for item in fixture_items()])
        self.assertIsNone(self.app._operations_window)
        self.confirm.return_value = False
        self.action.reset_mock()
        page.system_buttons['empty_recycle_bin'].invoke()
        self.action.assert_not_called()

    def test_busy_entry_does_not_queue_work_and_english_switch_preserves_page(self):
        self.app.busy = True
        self.app.navigate('system')
        page = self.app.views['system']
        self.read.assert_not_called()
        self.assertFalse(page.refresh_button.enabled)
        self.app.busy = False
        page.update_summary()
        page.refresh_button.invoke()
        self.pump(lambda: not self.app.busy)
        self.app.apply_settings(dict(self.app.settings, language='en'))
        self.assertEqual(self.app.nav_widgets['system'][2].cget('text'), 'System Management')
        self.assertEqual(i18n.tr(page.refresh_button.caption), 'Refresh')
        self.assertIs(self.app.views['system'], page)
        self.assertTrue(all(button.enabled for button in page.system_buttons.values()))

    def test_component_analysis_shows_live_progress_and_keeps_report_after_refresh(self):
        page = self.open_page()
        finish = threading.Event()
        report = '组件存储信息：\n实际大小：4.75 GB\n建议清理：否'
        def work(action, progress, **kwargs):
            progress('正在分析 Windows 组件存储…')
            progress(dict(percent=42.5))
            finish.wait(6)
            return dict(ok=True, output=report, message='组件分析已完成。')
        self.action.side_effect = work
        try:
            page.system_buttons['analyze_component_store'].invoke()
            self.pump(lambda: page.progress_percent.get() == '42.5%')
            self.assertTrue(self.app.busy)
            self.assertTrue(page.progress_frame.winfo_ismapped())
            self.assertEqual(str(page.operation_progress.cget('mode')), 'determinate')
            self.assertFalse(page.refresh_button.enabled)
            self.assertTrue(all(not button.enabled for button in page.system_buttons.values()))
            self.app.navigate('home')
            self.app.navigate('system')
            self.assertEqual(page.progress_value.get(), 42.5)
        finally:
            finish.set()
        self.pump(lambda: not self.app.busy)
        self.assertTrue(self.action.call_args.kwargs['allow_elevation'])
        self.assertEqual(page.progress_value.get(), 100)
        self.assertTrue(page.analysis_frame.winfo_ismapped())
        self.assertEqual(page.analysis_text.get('1.0', 'end-1c'), report)
        self.assertEqual(page.rows['component_store'][0].cget('text'), '已分析')
        page.refresh_button.invoke()
        self.pump(lambda: not self.app.busy)
        self.assertEqual(page.analysis_text.get('1.0', 'end-1c'), report)
        self.app.apply_settings(dict(self.app.settings, language='en'))
        self.assertEqual(page.analysis_text.get('1.0', 'end-1c'), report)
        self.assertEqual(page.rows['component_store'][0].cget('text'), 'Analyzed')

    def test_waiting_without_percent_and_cancelled_authorization_are_visible(self):
        page = self.open_page()
        finish = threading.Event()
        def work(action, progress, **kwargs):
            progress('等待 Windows 管理员授权…')
            finish.wait(6)
            return dict(ok=False, cancelled=True, message='已取消管理员授权，组件分析未执行。')
        self.action.side_effect = work
        try:
            page.system_buttons['analyze_component_store'].invoke()
            self.pump(lambda: page.progress_text.get() == '等待 Windows 管理员授权…')
            self.assertTrue(page.progress_frame.winfo_ismapped())
            self.assertEqual(str(page.operation_progress.cget('mode')), 'indeterminate')
            self.assertEqual(page.progress_percent.get(), '…')
        finally:
            finish.set()
        self.pump(lambda: not self.app.busy)
        self.assertEqual(page.progress_percent.get(), '')
        self.assertIn('未执行', page.progress_text.get())
        self.assertIn('取消', page.note.get())
        self.assertFalse(page.analysis_frame.winfo_ismapped())
        self.assertTrue(page.system_buttons['analyze_component_store'].enabled)


if __name__ == '__main__':
    unittest.main()

# Purpose: Regression tests for system Management page
