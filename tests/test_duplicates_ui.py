"""Current desktop duplicate workflows over workspace-owned sample files."""
from contextlib import ExitStack
import gc
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.ui import desktop
from cplus_cleaner.features.duplicates import duplicates_ui
from cplus_cleaner.application import preferences


class DuplicatesPageTests(unittest.TestCase):
    def setUp(self):
        self.gc_enabled = gc.isenabled()
        gc.disable()
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix='duplicates-ui-', dir=core.APP_DIR.parent)
        self.root = Path(self.temp.name)
        self.assertTrue(core.inside(self.root.resolve(), core.APP_DIR.parent.resolve()))
        self.files = self.root/'files'
        self.files.mkdir()
        for name in ('a.bin', 'b.bin'):
            (self.files/name).write_bytes(b'workspace duplicate fixture'*100)
        (self.files/'different.txt').write_bytes(b'keep this unique file')
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(core, 'DATA_DIR', self.root/'state'))
        self.stack.enter_context(patch.object(preferences, 'load', return_value=dict(preferences.DEFAULTS)))
        self.stack.enter_context(patch.object(preferences, 'read_autostart', return_value=False))
        self.stack.enter_context(patch.object(desktop, 'disk_snapshot', return_value=dict(root='C:\\', total=100, used=70, free=30)))
        self.error = self.stack.enter_context(patch.object(desktop.messagebox, 'showerror'))
        self.window = desktop.DesktopApplication()
        self.window.geometry('980x650+20+20')
        self.window.attributes('-topmost', True)
        self.errors = []
        self.window.report_callback_exception = lambda kind, error, trace: self.errors.append(str(error))
        self.window.navigate('duplicates')
        self.page = self.window.views['duplicates']
        self.page.folder.set(str(self.files))
        self.window.update()

    def tearDown(self):
        self.window.cancel.set()
        self.pump(lambda: not self.window.busy)
        for timer in self.window.tk.call('after', 'info'):
            self.window.tk.call('after', 'cancel', timer)
        self.window.destroy()
        self.assertEqual(self.errors, [])
        self.error.assert_not_called()
        self.stack.close()
        self.temp.cleanup()
        gc.collect()
        if self.gc_enabled:
            gc.enable()

    def pump(self, condition, seconds=6):
        deadline = time.monotonic()+seconds
        self.window.update()
        while not condition() and time.monotonic() < deadline:
            self.window.update()
            time.sleep(.01)
        self.window.update()
        self.assertTrue(condition(), 'duplicate page timed out')

    def scan(self):
        self.page.scan_button.invoke()
        self.pump(lambda: not self.window.busy)

    def test_current_page_scans_hash_groups_and_deletes_only_smart_selected_copy(self):
        self.assertIsInstance(self.page, duplicates_ui.DuplicatesPage)
        self.assertEqual(self.page.scan_button.caption, '开始扫描')
        self.assertEqual(self.page.clean_button.caption, '删除')
        self.assertFalse(self.window.status_label.winfo_ismapped())
        self.scan()
        self.assertEqual(len(self.page.records), 2)
        self.assertEqual(len(self.page.groups), 1)
        self.assertEqual(self.page.checked, set())
        self.page.smart_select()
        self.assertEqual(len(self.page.checked), 1)
        selected = self.page.records[next(iter(self.page.checked))]
        retained = next(item for iid, item in self.page.records.items() if iid not in self.page.checked)
        digest = core.sha256_file(Path(retained.path))
        self.page.clean_button.invoke()
        self.pump(lambda: not self.window.busy)
        self.assertFalse(Path(selected.path).exists())
        self.assertEqual(core.sha256_file(Path(retained.path)), digest)
        self.assertTrue((self.files/'different.txt').exists())
        self.assertEqual(self.window.nav_metrics['duplicates']['value'], 0)
        self.assertIn('已删除 1 个文件', self.page.scan_note.get())

    def test_selecting_every_copy_is_rejected_before_any_deletion(self):
        self.scan()
        self.page.select(True)
        with patch.object(core, 'delete_files') as delete:
            self.page.clean_button.invoke()
            self.window.update()
            delete.assert_not_called()
        self.assertTrue((self.files/'a.bin').exists())
        self.assertTrue((self.files/'b.bin').exists())
        self.error.assert_called_once()
        self.error.reset_mock()

    def test_changed_retained_copy_rejects_delete_and_preserves_both_files(self):
        self.scan()
        self.page.smart_select()
        retained = next(item for iid, item in self.page.records.items() if iid not in self.page.checked)
        Path(retained.path).write_bytes(b'changed retained file')
        with patch.object(core, 'delete_files') as delete:
            self.page.clean_button.invoke()
            self.pump(lambda: not self.window.busy)
            delete.assert_not_called()
        self.assertTrue((self.files/'a.bin').exists())
        self.assertTrue((self.files/'b.bin').exists())
        self.assertIn('删除未完成', self.page.scan_note.get())
        self.error.assert_called_once()
        self.error.reset_mock()

    def test_scan_runs_on_worker_and_cancel_keeps_found_groups(self):
        groups = core.duplicate_files(self.files)
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        worker = []
        def scan(*args):
            worker.append(threading.get_ident())
            started.set()
            release.wait(5)
            return groups
        with patch.object(core, 'duplicate_files', side_effect=scan):
            self.page.scan_button.invoke()
            self.pump(started.is_set)
            self.assertTrue(self.window.busy)
            self.assertNotEqual(worker[0], threading.get_ident())
            self.window.cancel.set()
            release.set()
            self.pump(lambda: not self.window.busy)
        self.assertEqual(len(self.page.records), 2)
        self.assertIn('扫描已取消', self.page.scan_note.get())
        self.assertTrue(self.page.scan_button.enabled)
        self.assertEqual(self.page.checked, set())


if __name__ == '__main__':
    unittest.main()

# Purpose: Regression tests for duplicate scan, retention and deletion UI
