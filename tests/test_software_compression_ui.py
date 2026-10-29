"""UI fixtures never compress or restore installed software."""
import gc
import os
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.ui import desktop
from cplus_cleaner.application import preferences
from cplus_cleaner.features.app_compression import software_compression as backend
from cplus_cleaner.features.app_compression import software_compression_ui as ui
from cplus_cleaner.ui.ui_widgets import check_images


class CompressionPageTests(unittest.TestCase):
    def setUp(self):
        # Baseline captions must not depend on the runner's Windows language.
        language = patch.object(desktop.i18n, 'detect_language', return_value='zh')
        language.start()
        self.addCleanup(language.stop)
        self.gc_enabled = gc.isenabled()
        gc.disable()
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix='compression-ui-', dir=core.APP_DIR.parent)
        self.base = Path(self.temp.name)
        self.items = [backend.Software('a', 'Alpha App', self.base/'a', 2048),
                      backend.Software('b', 'Beta App', self.base/'b', 4096),
                      backend.Software('c', 'Alpha Protected', self.base/'c', 1024, blocked='fixture protected')]
        self.real_history = backend.history
        self.patches = [patch.object(core, 'DATA_DIR', self.base/'state'),
                        patch.object(preferences, 'load', return_value=dict(preferences.DEFAULTS)),
                        patch.object(preferences, 'read_autostart', return_value=False),
                        patch.object(backend, 'scan', return_value=dict(items=self.items, errors=[], cancelled=False)),
                        patch.object(backend, 'history', return_value=[]),
                        patch.object(desktop.messagebox, 'showerror')]
        self.mocks = [p.start() for p in self.patches]
        self.window = desktop.DesktopApplication()
        self.window.geometry('980x650+20+20')
        self.errors = []
        self.window.report_callback_exception = lambda kind, error, trace: self.errors.append(str(error))
        self.page = ui.SoftwareCompressionPage(self.window.notebook, self.window)
        self.window.views['compress'] = self.page
        self.window.notebook.add(self.page, text='compress')
        self.window.current_page = 'compress'
        self.window.notebook.select(self.page)
        self.page.on_open()
        self.mocks[3].assert_not_called()
        self.page.scan_button.invoke()
        self.pump(lambda: self.page._loaded and not self.window.busy)

    def tearDown(self):
        self.window.cancel.set()
        self.pump(lambda: not self.window.busy and not self.page.queue_active)
        for timer in self.window.tk.call('after', 'info'):
            self.window.tk.call('after', 'cancel', timer)
        self.window.destroy()
        self.assertEqual(self.errors, [])
        self.mocks[-1].assert_not_called()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()
        gc.collect()
        if self.gc_enabled:
            gc.enable()

    def pump(self, condition, seconds=8):
        deadline = time.monotonic()+seconds
        self.window.update()
        while not condition() and time.monotonic() < deadline:
            self.window.update()
            time.sleep(.01)
        self.window.update()
        self.assertTrue(condition(), 'compression UI timed out')

    def select(self, key):
        self.page.rows[key].checkbox.invoke()
        self.window.update()

    def result(self, item, operation='compress', status='success'):
        return dict(status=status, item=replace(item, after=item.size//2 if operation == 'compress' else item.size,
                                                compressed=operation == 'compress'),
                    before=item.size, after=item.size//2 if operation == 'compress' else item.size,
                    saved=item.size//2 if operation == 'compress' else 0, errors=[], cancelled=False)

    def test_manual_readonly_scan_default_sort_warning_and_shared_checkmarks(self):
        self.mocks[3].assert_called_once()
        self.assertEqual(list(self.page.rows), ['b', 'a'])
        self.assertEqual(self.page.selected, set())
        self.assertIn('发现可压缩软件 2 款', self.page.heading.get())
        self.assertEqual(self.window.nav_metrics['compress']['text'], '6KB')
        self.assertEqual(str(self.page.warning.cget('image')), str(self.window.images.get('status-warning.png')))
        self.assertEqual(self.page.warning_image.width(), 44)
        for row in self.page.rows.values():
            self.assertEqual(row.after.cget('text'), '--')
            self.assertEqual(row.checkbox.cget('selectimage'), str(check_images(self.window)['on']))
        self.page.on_open()
        self.pump(lambda: not self.window.busy)
        self.mocks[3].assert_called_once()

    def test_sidebar_reentry_keeps_results_until_manual_scan(self):
        self.window.home_page.results['compress'] = dict(value=6144, payload=dict(items=self.items), error='')
        self.window.home_page.adopted.add('compress')
        self.mocks[3].return_value = dict(items=self.items[:1], errors=[], cancelled=False)
        self.window.navigate('home')
        self.window.nav_widgets['compress'][2].event_generate('<Button-1>')
        self.pump(lambda: not self.window.busy)
        self.mocks[3].assert_called_once()
        self.assertEqual(list(self.page.rows), ['b', 'a'])
        self.assertIn('compress', self.window.home_page.results)
        self.page.scan_button.invoke()
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.mocks[3].call_count, 2)
        self.assertEqual(list(self.page.rows), ['a'])

    def test_repeated_sidebar_clicks_never_queue_scan_even_while_busy(self):
        ready, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def job():
            ready.set()
            release.wait(5)
        self.window.start('只读样本任务', job, lambda result: None)
        self.pump(ready.is_set)
        for _ in range(8):
            self.window.nav_widgets['compress'][2].event_generate('<Button-1>')
        self.window.update()
        self.assertIsNone(self.page._pending_open)
        self.assertFalse(self.page.scan_button.enabled)
        self.mocks[3].assert_called_once()
        release.set()
        self.pump(lambda: not self.window.busy)
        self.assertTrue(self.page.scan_button.enabled)
        deadline = time.monotonic()+.2
        self.pump(lambda: time.monotonic() >= deadline)
        self.mocks[3].assert_called_once()
        self.page.scan_button.invoke()
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.mocks[3].call_count, 2)

    def test_buttons_match_and_actions_visible_at_minimum_window_size(self):
        self.assertEqual(self.page.scan_button.caption, '扫描')
        self.assertEqual(self.page.compress_button.caption, '压缩')
        self.assertEqual(self.page.scan_button.winfo_width(), self.page.compress_button.winfo_width())
        self.assertEqual(self.page.scan_button.winfo_height(), self.page.compress_button.winfo_height())
        self.assertLess(self.page.scan_button.winfo_rootx(), self.page.compress_button.winfo_rootx())
        self.select('a')
        for button in (self.page.scan_button, self.page.compress_button):
            self.assertIs(type(button), desktop.PillButton)
            self.assertEqual((button.winfo_width(), button.winfo_height()), (145, 43))
            self.assertEqual(button.itemcget(button.find_all()[0], 'fill'), desktop.MINT)
        action = self.page.rows['a'].action
        self.assertIs(type(action), desktop.PillButton)
        self.assertEqual((action.winfo_width(), action.winfo_height()), (104, 32))
        self.assertEqual(action.itemcget(action.find_all()[0], 'fill'), desktop.MINT)
        self.page.scan_button.paint(True)
        self.assertEqual(self.page.scan_button.itemcget(self.page.scan_button.find_all()[0], 'fill'), '#36c890')
        self.page.scan_button.set_enabled(False)
        self.assertEqual(self.page.scan_button.itemcget(self.page.scan_button.find_all()[0], 'fill'), '#dce9e4')
        self.page.scan_button.set_enabled(True)
        for row in self.page.rows.values():
            self.assertEqual(row.HEIGHT, 67)
            self.assertLess(row.before.winfo_rootx(), row.after.winfo_rootx())
            self.assertLessEqual(row.action.winfo_rootx()+row.action.winfo_width(), self.window.winfo_rootx()+980)
        numeric = self.page.heading_label.find_withtag('metric-number')
        self.assertEqual([self.page.heading_label.itemcget(token, 'text') for token in numeric], ['2', '1'])
        self.assertTrue(all(self.page.heading_label.itemcget(token, 'fill') == '#ff6269' for token in numeric))

    def test_filtered_selection_protects_blocked_software_and_keeps_hidden_selection(self):
        self.page.search.set('  ALPHA ')
        self.window.update()
        self.assertEqual(set(self.page.rows), {'a'})
        self.page.select_all_button.invoke()
        self.assertEqual(self.page.selected, {'a'})
        self.assertNotIn('c', self.page.rows)
        self.page.search.set('Beta')
        self.window.update()
        self.page.select_all_button.invoke()
        self.assertEqual(self.page.selected, {'a', 'b'})
        self.assertIn('已选择 2 款', self.page.heading.get())

    def test_rescan_clears_selection_never_compresses_and_updates_empty_badge(self):
        self.select('a')
        self.mocks[3].return_value = dict(items=[], errors=[])
        with patch.object(backend, 'compress') as action, patch.object(backend, 'restore') as restore:
            self.page.scan_button.invoke()
            self.pump(lambda: not self.window.busy)
            action.assert_not_called()
            restore.assert_not_called()
        self.assertFalse(self.page.selected)
        self.assertFalse(self.page.rows)
        self.assertEqual(self.window.nav_metrics['compress']['text'], '0KB')

    def test_single_row_compress_and_restore_updates_size_and_green_action(self):
        with patch.object(backend, 'compress', side_effect=lambda item, **kwargs: self.result(item)) as compress:
            self.page.rows['a'].action.invoke()
            self.pump(lambda: not self.page.queue_active and not self.window.busy)
        compress.assert_called_once()
        self.assertEqual(compress.call_args.args[0].key, 'a')
        row = self.page.rows['a']
        self.assertEqual(row.after.cget('text'), '1KB')
        self.assertEqual(row.action.caption, '还原')
        self.assertIs(type(row.action), desktop.PillButton)
        self.assertEqual((row.action.winfo_width(), row.action.winfo_height()), (104, 32))
        self.assertEqual(row.action.itemcget(row.action.find_all()[0], 'fill'), desktop.MINT)
        self.assertEqual(row.checkbox.cget('state'), 'disabled')
        with patch.object(backend, 'restore', side_effect=lambda item, **kwargs: self.result(item, 'restore')) as restore:
            row.action.invoke()
            self.pump(lambda: not self.page.queue_active and not self.window.busy)
        restore.assert_called_once()
        self.assertEqual(self.page.rows['a'].action.caption, '压缩')

    def test_batch_serial_queue_continues_after_failure_and_skips_unselected(self):
        self.select('a')
        self.select('b')
        calls = []

        def compress(item, **kwargs):
            calls.append(item.key)
            return self.result(item) if item.key == 'b' else dict(status='failed', errors=['fixture failure'])

        with patch.object(backend, 'compress', side_effect=compress):
            self.page.compress_button.invoke()
            self.pump(lambda: not self.page.queue_active and not self.window.busy)
        self.assertEqual(calls, ['a', 'b'])
        self.assertEqual(self.page.states['a'], 'failed')
        self.assertEqual(self.page.rows['b'].action.caption, '还原')
        self.assertTrue(self.page.scan_button.enabled)
        self.assertIn('失败 1 款', self.page.note.get())
        self.assertEqual(self.page.progress_value.get(),100)

    def test_cancel_keeps_partial_result_and_does_not_start_remaining_software(self):
        def compress(item, **kwargs):
            kwargs['progress'](dict(message='fixture progress',progress=40,completed=4,total=10))
            kwargs['cancel'].set()
            result = self.result(item, status='partial')
            result['cancelled'] = True
            return result

        with patch.object(backend, 'compress', side_effect=compress) as action:
            self.page.act_items(self.items[:2])
            self.pump(lambda: not self.page.queue_active and not self.window.busy)
        action.assert_called_once()
        self.assertEqual(self.page.states['a'], 'partial')
        self.assertEqual(self.page.states['b'], 'cancelled')
        self.assertEqual(self.page.rows['a'].after.cget('text'), '1KB')
        self.assertTrue(self.page.scan_button.enabled)
        self.assertEqual(self.page.progress_value.get(),20)
        self.assertIn('已取消',self.page.progress_caption.cget('text'))

    def test_live_compress_and_restore_progress_is_visible_and_finishes_on_result(self):
        for operation,value in (('compress',42),('restore',68)):
            with self.subTest(operation=operation):
                entered,release=threading.Event(),threading.Event()
                def work(item,**kwargs):
                    kwargs['progress'](dict(message='fixture progress',progress=value,completed=value,total=100))
                    entered.set();release.wait(5)
                    return self.result(item,operation)
                current=next(item for item in self.page.apps if item.key=='a')
                history=self.page.open_history() if operation=='restore' else None
                if history is not None:self.pump(lambda:not self.window.busy)
                with patch.object(backend,operation,side_effect=work):
                    try:
                        self.page.act_items([current],operation)
                        self.pump(lambda:entered.is_set() and self.page.progress_value.get()==value)
                        self.assertTrue(self.page.progress_bar.winfo_ismapped())
                        self.assertIn('1/1',self.page.progress_caption.cget('text'))
                        self.assertIn('还原' if operation=='restore' else '压缩',self.page.progress_caption.cget('text'))
                        self.assertLessEqual(self.page.progress_bar.winfo_rootx()+self.page.progress_bar.winfo_width(),self.window.winfo_rootx()+980)
                        if history is not None:
                            self.assertTrue(history.progress_bar.winfo_ismapped())
                            self.assertEqual(float(history.progress_bar['value']),value)
                    finally:release.set()
                    self.pump(lambda:not self.page.queue_active and not self.window.busy)
                self.assertEqual(self.page.progress_value.get(),100)
                self.assertIn('完成',self.page.progress_caption.cget('text'))

    def test_batch_progress_combines_current_software_with_completed_software(self):
        releases=[threading.Event(),threading.Event()]
        entered=[threading.Event(),threading.Event()]
        calls=[]
        def work(item,**kwargs):
            index=len(calls);calls.append(item.key)
            kwargs['progress'](dict(message='fixture batch',progress=50,completed=5,total=10))
            entered[index].set();releases[index].wait(5)
            return self.result(item)
        with patch.object(backend,'compress',side_effect=work):
            try:
                self.page.act_items(self.items[:2])
                self.pump(lambda:entered[0].is_set() and self.page.progress_value.get()==25)
                self.assertIn('1/2',self.page.progress_caption.cget('text'))
                releases[0].set()
                self.pump(lambda:entered[1].is_set() and self.page.progress_value.get()==75)
                self.assertIn('2/2',self.page.progress_caption.cget('text'))
            finally:
                for release in releases:release.set()
            self.pump(lambda:not self.page.queue_active and not self.window.busy)
        self.assertEqual(self.page.progress_value.get(),100)

    def test_long_file_activity_keeps_animating_and_stops_after_completion(self):
        entered,release=threading.Event(),threading.Event()
        def work(item,**kwargs):
            kwargs['progress'](dict(progress=24,completed=104,total=427,busy=True,
                current_file=r'D:\leidian\LDPlayer9\vms\data.vmdk',current_file_size=6*1024**3,elapsed=8))
            entered.set();release.wait(10)
            return self.result(item)
        with patch.object(backend,'compress',side_effect=work):
            try:
                self.page.rows['a'].action.invoke()
                self.pump(lambda:entered.is_set() and self.page._activity_running)
                self.assertEqual(self.page.progress_value.get(),24)
                self.assertTrue(self.page.activity_bar.winfo_ismapped())
                value=float(self.page.activity_bar['value'])
                self.pump(lambda:float(self.page.activity_bar['value'])!=value)
                self.assertIn('data.vmdk',self.page.progress_caption.cget('text'))
                self.assertIn('已用时 8 秒',self.page.progress_caption.cget('text'))
                window=self.page.open_history()
                self.window.update()
                self.assertTrue(window.activity_bar.winfo_ismapped())
            finally:release.set()
            self.pump(lambda:not self.window.busy and not self.page.queue_active)
        self.assertFalse(self.page._activity_running)
        self.assertFalse(self.page.activity_bar.winfo_ismapped())
        self.assertFalse(window.activity_bar.winfo_ismapped())
        self.assertEqual(self.page.progress_value.get(),100)

    def test_history_is_single_window_and_restore_uses_exact_record(self):
        record = dict(id='fixture-record', name='Alpha App', install_path=str(self.base/'a'),
                      old_size=2048, new_size=1024, time='2026/10/24 02:00', restored=False, can_restore=True)
        self.mocks[4].return_value = [record]
        first = self.page.open_history()
        second = self.page.open_history()
        self.assertIs(first, second)
        self.pump(lambda: not self.window.busy)
        self.assertEqual(len(first.record_buttons), 1)
        button = first.record_buttons[0][0]
        self.assertEqual(button.caption, '还原')
        self.assertFalse(button.light)
        self.assertIs(type(button), desktop.PillButton)
        self.assertEqual((int(button.cget('width')), int(button.cget('height'))), (104, 32))

        def restore(value, **kwargs):
            self.mocks[4].return_value = [dict(record, restored=True, can_restore=False)]
            return dict(status='success', before=1024, after=2048, errors=[])

        with patch.object(backend, 'restore', side_effect=restore) as action:
            button.invoke()
            self.assertFalse(button.enabled)
            button.invoke()
            self.pump(lambda: not self.page.queue_active and not self.window.busy)
            first.record_buttons[0][0].invoke()
            action.assert_called_once()
        self.assertEqual(action.call_args.args[0], record)
        self.assertEqual(first.record_buttons[0][0].caption, '已还原')
        self.assertFalse(first.record_buttons[0][0].enabled)
        first.destroy()
        self.assertIsNone(self.page._history_window)

    def test_finished_partial_restore_reaches_100_and_retains_retry_for_remaining_files(self):
        record=dict(id='partial',name='Alpha App',old_size=2048,new_size=1024,
                    schema=2,saved_bytes=1024,current_size=2048,can_restore=True,restored=False,status='partial')
        self.mocks[4].return_value=[record]
        window=self.page.open_history()
        self.pump(lambda:not self.window.busy)
        def restore(item,**kwargs):
            kwargs['progress'](dict(progress=76,completed=76,total=100))
            return dict(status='partial',errors=['fixture locked file'],cancelled=False)
        with patch.object(backend,'restore',side_effect=restore):
            window.record_buttons[0][0].invoke()
            self.pump(lambda:not self.page.queue_active and not self.window.busy)
        self.assertEqual(self.page.progress_value.get(),100)
        self.assertEqual(window.progress_percent.cget('text'),'100%')
        self.assertIn('部分完成',window.progress_caption.cget('text'))
        self.assertTrue(window.record_buttons[0][0].enabled)
        self.assertEqual(window.record_buttons[0][0].caption,'继续还原')
        self.assertIn('fixture locked file',window.result_note.cget('text'))
        self.assertTrue(window.result_note.winfo_ismapped())
        self.assertEqual(window.progress_percent.cget('fg'),'#ff6269')
        self.assertEqual(window.progress_bar.cget('style'),'Compression.Error.Horizontal.TProgressbar')

    def test_verified_legacy_restore_stays_disabled_after_reload_and_reopen(self):
        root=self.base/'a';root.mkdir()
        payload=root/'app.log';payload.write_bytes(b'already restored')
        info=root.stat()
        record=dict(id='a'*32,name='Alpha App',install_path=str(root),old_size=2048,new_size=2048,
                    processed=1,pending=0,status='partial',restored=False,superseded=False)
        snapshot=dict(key='a',name='Alpha App',folder=str(root),root=[info.st_dev,info.st_ino],
                      files=[dict(relative='app.log',identity=backend._identity(payload.lstat()))])
        backend._store(record,snapshot)
        with patch.object(backend,'history',wraps=self.real_history):
            window=self.page.open_history()
            self.pump(lambda:not self.window.busy)
            self.assertEqual(window.record_buttons[0][0].caption,'已还原')
            self.assertFalse(window.record_buttons[0][0].enabled)
            self.page._reload_history()
            self.assertEqual(window.record_buttons[0][0].caption,'已还原')
            window.destroy()
            reopened=self.page.open_history()
            self.pump(lambda:not self.window.busy)
            self.assertEqual(reopened.record_buttons[0][0].caption,'已还原')
            self.assertFalse(reopened.record_buttons[0][0].enabled)

    @unittest.skipUnless(os.name=='nt','Windows transparent compression')
    def test_real_history_button_recovers_after_zero_processed_failure(self):
        root=self.base/'a';root.mkdir()
        payload=root/'VBox.log.1';payload.write_bytes(b'restore retry fixture\n'*10000)
        digest=core.sha256_file(payload)
        outcome=backend._compact([payload],True)
        self.assertEqual(outcome['returncode'],0,outcome)
        self.assertTrue(backend._file_compressed(payload))
        info=root.stat()
        record=dict(id='b'*32,name='Alpha App',install_path=str(root),old_size=payload.stat().st_size,
                    new_size=backend.allocated_size(payload),processed=1,pending=0,remaining=1,
                    status='partial',restored=False,superseded=False)
        snapshot=dict(key='a',name='Alpha App',folder=str(root),root=[info.st_dev,info.st_ino],
                      files=[dict(relative=payload.name,identity=backend._identity(payload.lstat()))])
        backend._store(record,snapshot)
        actual_restore=backend.restore
        calls=[]
        def work(value,**kwargs):
            calls.append(value['id'])
            if len(calls)==1:
                def locked(paths,mode):raise PermissionError('fixture locked log')
                return actual_restore(value,runner=locked,**kwargs)
            return actual_restore(value,**kwargs)
        with patch.object(backend,'history',wraps=self.real_history),patch.object(backend,'restore',side_effect=work):
            window=self.page.open_history()
            self.pump(lambda:not self.window.busy)
            window.record_buttons[0][0].invoke()
            self.pump(lambda:not self.page.queue_active and not self.window.busy)
            self.assertEqual(backend._records()[0]['processed'],0)
            self.assertEqual(window.record_buttons[0][0].caption,'继续还原')
            self.assertIn('fixture locked log',window.result_note.cget('text'))
            window.record_buttons[0][0].invoke()
            self.pump(lambda:not self.page.queue_active and not self.window.busy)
            self.assertEqual(window.record_buttons[0][0].caption,'已还原')
            self.assertFalse(window.record_buttons[0][0].enabled)
            self.assertEqual(window.progress_percent.cget('fg'),'#27b77d')
            self.assertEqual(window.progress_percent.cget('text'),'100%')
        self.assertEqual(len(calls),2)
        self.assertFalse(backend._file_compressed(payload))
        self.assertEqual(core.sha256_file(payload),digest)

    def test_superseded_history_is_visible_and_cannot_restore(self):
        self.mocks[4].return_value = [dict(id='old', name='Old App', old_size=2048, new_size=1024,
                                         superseded=True, can_restore=True, restored=False)]
        window = self.page.open_history()
        self.pump(lambda: not self.window.busy)
        button, record = window.record_buttons[0]
        self.assertEqual(button.caption, '记录已更新')
        self.assertFalse(button.enabled)
        with patch.object(backend, 'restore') as action:
            self.page.act_items([record], 'restore')
            action.assert_not_called()

    def test_busy_queue_waits_and_new_action_clears_previous_cancellation(self):
        self.window.busy = True
        self.page.queue = [self.items[0]]
        self.page.queue_active = True
        self.page._advance_queue()
        self.assertEqual(self.page.queue, [self.items[0]])
        self.window.busy = False
        self.page._queue_cancelled = True
        self.pump(lambda: not self.page.queue_active)
        self.window.cancel.set()
        with patch.object(backend, 'compress', side_effect=lambda item, **kwargs: self.result(item)) as action:
            self.page.act_items([self.items[0]])
            self.pump(lambda: not self.page.queue_active and not self.window.busy)
        action.assert_called_once()


if __name__ == '__main__':
    unittest.main()

# Purpose: Regression tests for app Compression page and progress
