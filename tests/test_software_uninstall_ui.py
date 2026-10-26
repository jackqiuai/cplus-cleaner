import gc
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.ui import desktop
from cplus_cleaner.application import preferences
from cplus_cleaner.features.uninstall import software_uninstall as backend
from cplus_cleaner.features.uninstall import software_uninstall_ui as ui
from cplus_cleaner.ui.ui_widgets import check_images


class UninstallPageTests(unittest.TestCase):
    def setUp(self):
        # Baseline captions must not depend on the runner's Windows language.
        language = patch.object(desktop.i18n, 'detect_language', return_value='zh')
        language.start()
        self.addCleanup(language.stop)
        self.gc_enabled = gc.isenabled();gc.disable();gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix='uninstall-ui-',dir=core.APP_DIR.parent)
        self.base = Path(self.temp.name)
        self.items = [backend.Software('a','Alpha App',2048,'20261001',str(self.base),'never-run'),
            backend.Software('b','Beta App',4096,'20261023',str(self.base),'never-run'),
            backend.Software('c','alpha Tools',1024,'20260101',str(self.base),'never-run')]
        self.patches = [patch.object(core,'DATA_DIR',self.base/'state'),
            patch.object(preferences,'load',return_value=dict(preferences.DEFAULTS)),
            patch.object(preferences,'read_autostart',return_value=False),
            patch.object(ui,'disk_snapshot',return_value=dict(root='C:\\',total=100,used=70,free=30)),
            patch.object(backend,'scan',return_value=dict(items=self.items,errors=[],cancelled=False)),
            patch.object(desktop.messagebox,'showerror')]
        self.mocks = [p.start() for p in self.patches]
        self.window = desktop.DesktopApplication()
        self.window.geometry('980x650+20+20')
        self.window.attributes('-topmost',True)
        self.errors = []
        self.window.report_callback_exception = lambda kind,error,trace:self.errors.append(str(error))
        self.window.navigate('uninstall');self.page = self.window.views['uninstall']
        self.pump(lambda:self.page._loaded and not self.window.busy)
    def tearDown(self):
        self.window.cancel.set()
        if self.page._residue_window:
            self.page._residue_window.skip()
        self.pump(lambda:not self.window.busy)
        for timer in self.window.tk.call('after','info'):
            self.window.tk.call('after','cancel',timer)
        self.window.destroy()
        self.assertEqual(self.errors,[])
        self.mocks[-1].assert_not_called()
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup();gc.collect()
        if self.gc_enabled:gc.enable()
    def pump(self, condition, seconds=8):
        deadline = time.monotonic()+seconds
        self.window.update()
        while not condition() and time.monotonic()<deadline:
            self.window.update();time.sleep(.01)
        self.window.update();self.assertTrue(condition(),'uninstall UI timed out')
    def select(self, key):
        self.page.rows[key].checkbox.invoke();self.window.update()

    def test_default_scan_sort_icons_counts_and_matching_button_styles(self):
        self.mocks[4].assert_called_once()
        self.assertEqual(list(self.page.rows),['b','a','c'])
        self.assertEqual(self.window.nav_metrics['uninstall']['text'],'3款')
        self.assertFalse(self.page.selected)
        self.assertIn('已选择 0KB',self.page.heading.get())
        self.assertEqual(self.page.scan_button.winfo_width(),self.page.uninstall_button.winfo_width())
        self.assertEqual(self.page.scan_button.winfo_height(),self.page.uninstall_button.winfo_height())
        for row in self.page.rows.values():
            self.assertGreater(row.action.winfo_x(),row.name.winfo_x())
            self.assertLessEqual(row.action.winfo_rootx()+row.action.winfo_width(),self.window.winfo_rootx()+980)
            self.assertEqual(row.checkbox.cget('selectimage'),str(check_images(self.window)['on']))
    def test_search_only_name_and_select_all_only_filtered_items(self):
        self.page.search.set('  ALPHA ');self.window.update()
        self.assertEqual(set(self.page.rows),{'a','c'})
        self.page.select_all_button.invoke()
        self.assertEqual(self.page.selected,{'a','c'})
        self.assertIn('已选择 3KB',self.page.heading.get())
        self.page.search.set('Beta');self.window.update()
        self.page.select_all_button.invoke()
        self.assertEqual(self.page.selected,{'a','b','c'})
        self.assertIn('已选择 7KB',self.page.heading.get())
    def test_rescan_clears_selection_and_refreshes_zero(self):
        self.select('a')
        self.mocks[4].return_value=dict(items=[],errors=[])
        self.page.scan_button.invoke();self.pump(lambda:not self.window.busy)
        self.assertFalse(self.page.rows);self.assertFalse(self.page.selected)
        self.assertEqual(self.window.nav_metrics['uninstall']['text'],'0款')
    def test_queue_continues_after_failure_and_only_uninstalls_explicit_items(self):
        self.select('a');self.select('b')
        calls=[]
        def uninstall(item,**kwargs):
            calls.append(item.key)
            return dict(key=item.key,name=item.name,status='failed' if item.key=='a' else 'success',error='fixture error' if item.key=='a' else '')
        with patch.object(backend,'uninstall',side_effect=uninstall),patch.object(backend,'scan_residue',return_value=dict(files=[],registry=[],errors=[])):
            self.page.uninstall_button.invoke()
            self.pump(lambda:not self.page.queue_active and not self.window.busy)
        self.assertEqual(calls,['a','b'])
        self.assertEqual(self.page.states['a'],'failed')
        self.assertEqual({a.key for a in self.page.apps},{'a','c'})
        self.assertEqual(self.window.nav_metrics['uninstall']['text'],'2款')
    def test_residue_window_close_advances_queue_and_clean_uses_manifest(self):
        self.select('a');self.select('b')
        manifest=dict(token='mock',files=[dict(path=str(self.base/'fixture.dat'),size=10)],registry=[],errors=[])
        def uninstall(item,**kwargs):return dict(key=item.key,name=item.name,status='success',error='')
        with patch.object(backend,'uninstall',side_effect=uninstall) as action,\
                patch.object(backend,'scan_residue',side_effect=[manifest,dict(files=[],registry=[],errors=[])]),\
                patch.object(backend,'clean_residue',return_value=dict(deleted=1,errors=[],cancelled=False)) as clean:
            self.page.uninstall_button.invoke()
            self.pump(lambda:self.page._residue_window is not None)
            self.page._residue_window.clean_button.invoke()
            self.pump(lambda:not self.page.queue_active and not self.window.busy)
        self.assertEqual(action.call_count,2);clean.assert_called_once()
        self.assertEqual(clean.call_args.args[0]['token'],'mock')
    def test_residue_x_close_skips_and_continues_without_cleaning(self):
        manifest=dict(token='mock',files=[dict(path='preview only',size=1)],registry=[])
        self.select('a')
        with patch.object(backend,'uninstall',return_value=dict(status='success')),\
                patch.object(backend,'scan_residue',return_value=manifest),patch.object(backend,'clean_residue') as clean:
            self.page.uninstall_button.invoke();self.pump(lambda:self.page._residue_window is not None)
            self.page._residue_window.skip()
            self.pump(lambda:not self.page.queue_active)
            clean.assert_not_called()
    def test_waiting_queue_does_not_drop_next_item_when_another_job_is_busy(self):
        self.window.busy=True
        self.page.queue=[self.items[0]];self.page.queue_active=True
        self.page._advance_queue()
        self.assertEqual(self.page.queue,[self.items[0]])
        self.window.busy=False
        self.page._queue_cancelled=True
        self.pump(lambda:not self.page.queue_active)

    def test_new_explicit_queue_does_not_inherit_previous_task_cancellation(self):
        self.window.cancel.set()
        with patch.object(backend,'uninstall',return_value=dict(status='failed')) as action:
            self.page.uninstall_items([self.items[0]])
            self.pump(lambda:not self.page.queue_active and not self.window.busy)
        action.assert_called_once()

    def test_cancel_during_publisher_wait_does_not_start_remaining_items(self):
        def uninstall(item,**kwargs):
            kwargs['cancel'].set()
            return dict(status='cancelled',cancelled=True)
        with patch.object(backend,'uninstall',side_effect=uninstall) as action:
            self.page.uninstall_items(self.items[:2])
            self.pump(lambda:not self.page.queue_active and not self.window.busy)
        action.assert_called_once()
        self.assertEqual(self.page.states['b'],'cancelled')


if __name__=='__main__':unittest.main()

# Purpose: Regression tests for app Uninstaller page, search and selection
