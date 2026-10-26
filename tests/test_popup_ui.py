"""Actual Tk workflows with fixture software and mocked window observations."""
from dataclasses import replace
import gc
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.ui import desktop
from cplus_cleaner.features.popup import popup as backend
from cplus_cleaner.features.popup import popup_ui as ui
from cplus_cleaner.application import preferences
from tests.test_popup_huorong import vendor_rule
from tests.popup_fixtures import groups as fixture_groups


class PopupPageTests(unittest.TestCase):
    def setUp(self):
        # Baseline captions must not depend on the runner's Windows language.
        language = patch.object(desktop.i18n, 'detect_language', return_value='zh')
        language.start()
        self.addCleanup(language.stop)
        self.gc_enabled = gc.isenabled()
        gc.disable()
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix='popup-ui-', dir=core.APP_DIR.parent)
        self.base = Path(self.temp.name)
        self.items = fixture_groups(self.base)
        self.patches = [patch.object(core, 'DATA_DIR', self.base/'state'),
                        patch.object(preferences, 'load', return_value=dict(preferences.DEFAULTS)),
                        patch.object(preferences, 'read_autostart', return_value=False),
                        patch.object(backend, 'scan', return_value=dict(items=self.items, errors=[], cancelled=False)),
                        patch.object(backend.windows, 'snapshot', return_value=[]),
                        patch.object(backend.windows, 'post_close', return_value=False),
                        patch.object(backend.windows, 'disappeared', return_value=False),
                        patch.object(desktop.messagebox, 'showerror')]
        self.mocks = [item.start() for item in self.patches]
        self.window = desktop.DesktopApplication()
        self.window.geometry('980x650+20+20')
        self.window.attributes('-topmost', True)
        self.errors = []
        self.window.report_callback_exception = lambda kind, error, trace: self.errors.append(str(error))
        self.window.navigate('popup')
        self.page = self.window.views['popup']
        self.pump(lambda: self.page._loaded and not self.window.busy)

    def tearDown(self):
        self.window.cancel.set()
        self.pump(lambda: not self.window.busy)
        for timer in self.window.tk.call('after', 'info'):
            self.window.tk.call('after', 'cancel', timer)
        self.window.destroy()
        self.assertEqual(self.errors, [])
        self.mocks[-1].assert_not_called()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()
        gc.collect()
        if self.gc_enabled:
            gc.enable()

    def pump(self, condition, seconds=5):
        deadline = time.monotonic()+seconds
        self.window.update()
        while not condition() and time.monotonic() < deadline:
            self.window.update()
            time.sleep(.01)
        self.window.update()
        self.assertTrue(condition(), 'popup UI timed out')

    def test_first_visit_enables_scanned_groups_and_saves_window_rule_selection(self):
        self.assertIsInstance(self.page, ui.PopupPage)
        self.mocks[3].assert_called_once()
        self.assertEqual([len(item.rules) for item in self.page.items], [15, 2, 1])
        self.assertIn('3 个软件分组 18 项广告弹窗', self.page.heading.get())
        self.assertEqual(self.page.summary.get(),'告别弹窗骚扰')
        self.assertFalse(hasattr(self.page,'catalog_status_label'))
        self.assertEqual(self.window.nav_metrics['popup']['text'], '18项')
        self.assertTrue(self.page.blocker.enabled)
        self.assertTrue(all(row.switch.value for row in self.page.rows.values()))
        self.assertTrue(backend.Blocker().enabled)
        self.assertIs(self.page.warning_image, self.window.images.get('status-warning.png'))
        self.assertEqual(self.page.expanded, set())
        self.mocks[5].assert_not_called()
        self.window.navigate('home')
        self.window.navigate('popup')
        self.window.update()
        self.mocks[3].assert_called_once()

    def test_minimum_size_keeps_header_switches_and_history_visible(self):
        for widget in (self.page.scan_button, self.page.block_button, self.page.history_button):
            x = widget.winfo_rootx()+widget.winfo_width()//2
            y = widget.winfo_rooty()+widget.winfo_height()//2
            self.assertIs(self.window.winfo_containing(x, y), widget)
        self.assertGreaterEqual(self.page.heading_label.winfo_width(), self.page.heading_label.winfo_reqwidth())
        self.assertLessEqual(self.page.heading_label.winfo_rootx()+self.page.heading_label.winfo_width(), self.page.actions.winfo_rootx())
        self.assertFalse(self.window.footer.winfo_ismapped())
        numbers = self.page.heading_label.find_withtag('metric-number')
        self.assertEqual([self.page.heading_label.itemcget(item, 'text') for item in numbers], ['3', '18'])
        for token in numbers:
            self.assertEqual(self.page.heading_label.itemcget(token, 'fill'), '#ff6269')
            self.assertEqual(self.window.tk.call('font', 'actual', self.page.heading_label.itemcget(token, 'font'), '-weight'), 'bold')
        for row in self.page.rows.values():
            self.assertEqual((row.switch.winfo_width(), row.switch.winfo_height()), (40, 18))
            self.assertFalse(row.children_area.winfo_manager())

    def test_group_child_and_bulk_switches_update_effective_selection(self):
        item = self.page.items[0]
        row = self.page.rows[item.key]
        row.switch.invoke()
        self.assertFalse(self.page.blocker.group_enabled(item))
        self.assertEqual(self.page.blocker.selected_count(),3)
        self.page.toggle_expanded(item.key)
        self.window.update()
        self.assertTrue(row.children_area.winfo_ismapped())
        rule = item.rules[0]
        row.rule_rows[rule.id][1].invoke()
        self.assertTrue(self.page.blocker.enabled)
        self.assertTrue(self.page.blocker.group_enabled(item))
        self.assertEqual(self.page.blocker.selected_count(),4)
        self.assertEqual(self.page.block_button.caption, '全部取消')
        self.page.block_button.invoke()
        self.assertFalse(self.page.blocker.enabled)
        self.assertEqual(self.page.blocker.selected_count(),0)
        self.page.block_button.invoke()
        self.assertEqual(self.page.blocker.selected_count(), 18)
        self.assertEqual(self.window.nav_metrics['popup']['text'], '18项')

    def test_wheel_keeps_short_results_at_top_and_scrolls_only_expanded_overflow(self):
        page = self.page
        first = page.rows[page.items[0].key]
        self.assertLess(page.inner.winfo_reqheight(), page.canvas.winfo_height())
        initial_y = first.winfo_rooty()
        for delta in (120, 120, -120, -120, 120):
            page.canvas.event_generate('<MouseWheel>', x=20, y=15, delta=delta)
            self.window.update()
            self.assertEqual(first.winfo_rooty(), initial_y)
            self.assertEqual(page.canvas.yview(), (0.0, 1.0))
        page.canvas.yview_moveto(1)
        self.window.update()
        self.assertEqual(first.winfo_rooty(), initial_y)
        page.toggle_expanded(page.items[0].key)
        self.window.update()
        self.assertGreater(page.inner.winfo_reqheight(), page.canvas.winfo_height())
        page.canvas.event_generate('<MouseWheel>', x=20, y=15, delta=-120)
        self.window.update()
        self.assertGreater(page.canvas.yview()[0], 0)
        self.assertLess(first.winfo_rooty(), initial_y)
        page.canvas.yview_moveto(1)
        page.toggle_expanded(page.items[0].key)
        self.window.update()
        self.assertEqual(page.canvas.yview(), (0.0, 1.0))
        self.assertEqual(first.winfo_rooty(), initial_y)

    def test_clicking_qqnt_header_expands_real_rule_labels_without_changing_switches(self):
        definition=next(g for g in backend.catalog() if g.definition['name']=='qqnt')
        item=replace(definition,roots=(str(self.base),),
                     rules=tuple(backend.huorong.bind_rule(r,(str(self.base),)) for r in definition.rules),
                     icon=(core.APP_DIR/'assets/icon-qq.png').read_bytes())
        self.page.items=[item];self.page.blocker.set_items([item]);self.page.blocker.enable_detected()
        self.page.expanded.clear();self.page.render();self.window.update()
        row=self.page.rows[item.key]
        settings=self.base/'state/popup/state.json'
        saved=settings.read_bytes()
        with patch.object(backend.windows,'apply_vendor') as apply:
            for widget in (row.name,row.icon,row.header,row.name.master,row.positions,
                           row.count,row.switch.master,row.arrow):
                widget.event_generate('<Button-1>',x=5,y=5);self.window.update()
                self.assertIn(item.key,self.page.expanded)
                self.assertTrue(row.children_area.winfo_ismapped())
                self.assertEqual(len(row.rule_rows),2)
                for child, switch, count in row.rule_rows.values():
                    texts=[c.cget('text') for c in child.winfo_children() if isinstance(c,ui.tk.Label)]
                    self.assertIn('软件推荐',texts)
                    self.assertIn('右下窗口',texts)
                    self.assertTrue(switch.value)
                widget.event_generate('<Button-1>',x=5,y=5);self.window.update()
                self.assertNotIn(item.key,self.page.expanded)
                self.assertFalse(row.children_area.winfo_manager())
            self.assertEqual(settings.read_bytes(),saved)
            self.assertEqual((row.rule_icon.width(),row.rule_icon.height()),(16,16))
            row.header.focus_force();row.header.event_generate('<Return>');self.window.update()
            self.assertIn(item.key,self.page.expanded)
            row.switch.invoke();self.window.update()
            self.assertIn(item.key,self.page.expanded)
            self.assertEqual(self.page.blocker.selected_count(),0)
            row.rule_rows[item.rules[0].id][1].invoke();self.window.update()
            self.assertIn(item.key,self.page.expanded)
            self.assertEqual(self.page.blocker.selected_count(),1)
            row.header.event_generate('<space>');self.window.update()
            self.assertNotIn(item.key,self.page.expanded)
            apply.assert_not_called()

    def test_failed_setting_write_keeps_switch_and_engine_state(self):
        row = self.page.rows[self.page.items[0].key]
        with patch.object(backend, '_atomic', side_effect=OSError('fixture denied')):
            row.switch.invoke()
        self.assertTrue(row.switch.value)
        self.assertTrue(self.page.blocker.enabled)
        self.assertIn('fixture denied', self.page.note.get())
        self.mocks[5].assert_not_called()

    def test_restart_restores_enabled_blocking_without_leaving_home(self):
        self.assertTrue(self.page.blocker.enabled)
        for timer in self.window.tk.call('after', 'info'):
            self.window.tk.call('after', 'cancel', timer)
        self.window.destroy()
        self.window = desktop.DesktopApplication()
        self.window.report_callback_exception = lambda kind, error, trace: self.errors.append(str(error))
        self.pump(lambda: 'popup' in self.window.views and self.window.views['popup']._loaded and not self.window.busy)
        self.page = self.window.views['popup']
        self.assertEqual(self.window.current_page, 'home')
        self.assertTrue(self.page.blocker.enabled)
        self.assertEqual(self.window.nav_metrics['popup']['text'], '18项')
        self.assertEqual(self.mocks[3].call_count, 2)

    def test_rescan_replaces_inventory_and_empty_result_clears_badge(self):
        self.mocks[3].return_value = dict(items=[], errors=[], cancelled=False)
        self.page.scan_button.invoke()
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.page.items, [])
        self.assertEqual(self.page.rows, {})
        self.assertEqual(self.window.nav_metrics['popup']['text'], '0项')
        self.assertFalse(self.page.block_button.enabled)

    def test_rescan_shows_reading_then_actual_progress_and_retains_completion(self):
        registered, checking, release = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.addCleanup(checking.set)
        def scan(*, cancel, progress, stage_progress):
            registered.set()
            self.assertTrue(checking.wait(5))
            stage_progress(dict(message='已核对 5/15 款软件', completed=5, total=15))
            self.assertTrue(release.wait(5))
            return dict(items=self.items, errors=[], cancelled=False)
        self.mocks[3].side_effect = scan
        self.page.scan_button.invoke()
        self.pump(registered.is_set)
        self.assertTrue(self.page.progress_frame.winfo_ismapped())
        self.assertEqual(str(self.page.scan_progress.cget('mode')), 'indeterminate')
        self.assertEqual(self.page.progress_percent.get(), '读取中')
        self.assertFalse(self.page.scan_button.enabled)
        self.assertFalse(self.page.block_button.enabled)
        self.assertEqual(len(self.page.rows), 3)
        checking.set()
        self.pump(lambda: self.page.progress_percent.get() == '33%')
        self.assertEqual(str(self.page.scan_progress.cget('mode')), 'determinate')
        self.assertAlmostEqual(self.page.progress_value.get(), 100/3)
        self.assertLessEqual(self.page.scan_progress.winfo_rooty()+self.page.scan_progress.winfo_height(),
                             self.page.table_header.winfo_rooty())
        self.assertLessEqual(self.page.scan_progress.winfo_rootx()+self.page.scan_progress.winfo_width(),
                             self.window.winfo_rootx()+980)
        release.set()
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.page.progress_value.get(), 100)
        self.assertEqual(self.page.progress_percent.get(), '100%')
        self.assertEqual(self.page.progress_text.get(), '扫描完成')
        self.assertTrue(self.page.progress_frame.winfo_ismapped())
        self.page.scan_button.invoke()
        self.assertEqual(self.page._scan_fraction, 0)
        self.assertEqual(self.page.progress_percent.get(), '读取中')
        self.pump(lambda: not self.window.busy)

    def test_cancelled_rescan_keeps_partial_progress_and_reenables_actions(self):
        self.page.blocker.set_all(False)
        def scan(*, cancel, progress, stage_progress):
            stage_progress(dict(message='已核对 6/15 款软件', completed=6, total=15))
            cancel.set()
            return dict(items=self.items, errors=[], cancelled=True)
        self.mocks[3].side_effect = scan
        self.page.scan_button.invoke()
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.page.progress_value.get(), 40)
        self.assertEqual(self.page.progress_text.get(), '扫描已取消')
        self.assertTrue(self.page.scan_button.enabled)
        self.assertTrue(self.page.block_button.enabled)
        self.assertFalse(self.page.blocker.enabled)
        self.assertEqual(self.page.blocker.selected_count(),0)

    def test_rescan_enables_visible_results_and_hides_unsupported_rules(self):
        close=vendor_rule(identity='auto-close')
        hide=vendor_rule(action='hide',identity='auto-hide')
        kill=vendor_rule(action='kill',identity='auto-kill')
        bad=replace(close,id='auto-bad',supported=False,matcher=None,reason='unknown')
        mixed=backend.Software('auto-mixed','测试软件',(r'C:\Apps\Demo',),(close,hide,kill,bad),source='huorong')
        hidden=replace(mixed,key='auto-empty',rules=(bad,))
        self.page.blocker.set_all(False)
        self.mocks[3].return_value=dict(items=[mixed,hidden],errors=[],cancelled=False)
        with patch.object(backend.windows,'apply_vendor') as apply:
            self.page.scan_button.invoke();self.pump(lambda:not self.window.busy)
            self.assertEqual(len(self.page.rows),1)
            row=self.page.rows[mixed.key]
            self.assertTrue(row.switch.value)
            self.assertTrue(row.rule_rows[close.id][1].value)
            self.assertTrue(row.rule_rows[hide.id][1].value)
            self.assertFalse(row.rule_rows[kill.id][1].value)
            self.assertNotIn(bad.id,row.rule_rows)
            self.assertEqual(self.window.nav_metrics['popup']['text'],'3项')
            self.assertTrue(self.page.blocker.enabled)
            self.assertEqual(self.page.summary.get(),'告别弹窗骚扰')
            self.assertFalse(hasattr(self.page,'catalog_status_label'))
            apply.assert_not_called()

    def test_rescan_save_failure_keeps_disabled_state_and_reports_error(self):
        self.page.blocker.set_all(False)
        with patch.object(backend,'_atomic',side_effect=OSError('fixture save denied')):
            self.page.scan_button.invoke();self.pump(lambda:not self.window.busy)
        self.assertFalse(self.page.blocker.enabled)
        self.assertEqual(self.page.blocker.selected_count(),0)
        self.assertIn('fixture save denied',self.page.note.get())

    def test_failed_rescan_stops_progress_without_marking_complete(self):
        def scan(*, cancel, progress, stage_progress):
            stage_progress(dict(message='已核对 3/15 款软件', completed=3, total=15))
            raise OSError('fixture denied')
        self.mocks[3].side_effect = scan
        self.page.scan_button.invoke()
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.page.progress_value.get(), 20)
        self.assertEqual(self.page.progress_text.get(), '扫描未完成')
        self.assertEqual(str(self.page.scan_progress.cget('mode')), 'determinate')
        self.assertIn('fixture denied', self.page.note.get())
        self.assertEqual(len(self.page.rows), 3)
        self.assertTrue(self.page.scan_button.enabled)
        self.mocks[-1].assert_called_once()
        self.mocks[-1].reset_mock()

    def test_history_single_window_recent_records_and_clear_preserve_counts(self):
        first, second = self.page.items[0].rules[:2]
        self.page.blocker.state['counts'] = {first.id: 2, second.id: 3}
        self.page.blocker.state['history'] = {
            first.id: dict(time=time.time(), software='迅雷', title=first.label, rule_id=first.id, count=2),
            second.id: dict(time=time.time()-31*86400, software='迅雷', title=second.label, rule_id=second.id, count=3)}
        window = self.page.show_history()
        self.assertIs(window, self.page.show_history())
        self.assertEqual(len(window.tree.get_children()), 1)
        self.assertNotIn(' · 火绒',window.tree.item(window.tree.get_children()[0],'values')[0])
        self.assertIn('2 次', window.total_label.cget('text'))
        window.clear_button.invoke()
        self.assertEqual(self.page.blocker.history(), [])
        self.assertEqual(self.page.blocker.group_count(self.page.items[0]), 5)
        self.assertIn('暂无拦截记录', str(window.tree.item(window.tree.get_children()[0], 'values')))
        window.destroy()
        self.assertIsNone(self.page._history_window)

    def test_confirmed_counter_updates_while_another_page_is_open(self):
        item = self.page.items[0]
        rule = item.rules[0]
        observed = dict(hwnd=99991,pid=99992,created=123,image=str(Path(item.roots[0])/'Thunder.exe'),
                        title=rule.title,**{'class':rule.window_class},width=400,height=300,popup=True,
                        logical_width=400,logical_height=300,styles=0x80000000,exstyles=0,position=5)
        self.mocks[4].return_value=[observed]
        with patch.object(backend.windows,'enrich',return_value=observed),patch.object(backend.windows,'apply_vendor',return_value=True):
            self.window.navigate('home')
            self.pump(lambda:bool(self.page.blocker.pending))
            self.assertEqual(self.page.blocker.rule_count(rule),0)
            self.mocks[4].return_value=[];self.mocks[6].return_value=True
            self.pump(lambda:self.page.blocker.rule_count(rule)==1)
        self.assertEqual(self.page.rows[item.key].count.cget('text'),'1次')
        self.assertEqual(self.window.nav_metrics['popup']['text'],'18项')
        self.assertEqual(len(self.page.blocker.history()),1)

    def test_vendor_status_positions_and_enabled_action_defaults(self):
        self.page.blocker.set_all(False)
        close=vendor_rule(identity='ui-close')
        kill=vendor_rule(action='kill',identity='ui-kill')
        bad=replace(close,id='ui-unsupported',supported=False,reason='未知窗口条件',matcher=None)
        item=backend.Software('ui-vendor','示例软件',(r'C:\Apps\Demo',),(close,kill,bad),source='huorong')
        self.page.items=[item];self.page.blocker.set_items([item]);self.page.render()
        self.page.toggle_expanded(item.key);self.window.update()
        row=self.page.rows[item.key]
        self.assertEqual(row.display_name,item.name)
        self.assertIn('1 个软件分组',self.page.heading.get())
        self.assertFalse(hasattr(row,'status_labels'))
        self.assertNotIn(bad.id,row.rule_rows)
        self.assertIn('2 项广告弹窗',self.page.heading.get())
        self.assertTrue(row.rule_rows[kill.id][1].enabled)
        self.assertEqual(ui.position_text(0,'huorong'),'近似全屏')
        self.assertEqual(ui.position_text(5,'huorong'),'右下窗口')
        self.assertEqual(ui.position_text(0),'近似全屏')
        self.page.block_button.invoke()
        self.assertTrue(self.page.blocker.rule_enabled(close))
        self.assertFalse(self.page.blocker.rule_enabled(kill))
        row.rule_rows[kill.id][1].invoke()
        self.assertTrue(self.page.blocker.rule_enabled(kill))
        row.rule_rows[kill.id][1].invoke()
        self.assertFalse(self.page.blocker.rule_enabled(kill))
        self.mocks[5].assert_not_called()

    def test_vendor_actions_default_enabled_and_single_rules_toggle_without_permission_controls(self):
        rules = tuple(vendor_rule(action=action, identity='direct-'+(action or 'combined'))
                      for action in ('hide', '', 'kill'))
        item = backend.Software('direct-vendor', '测试软件', (r'C:\Apps\Demo',), rules, source='huorong')
        self.page.items = [item]
        self.page.blocker.set_items([item])
        self.page.render()
        self.page.toggle_expanded(item.key)
        self.window.update()
        self.assertFalse(hasattr(self.page, 'action_checks'))
        self.assertFalse(hasattr(self.page, 'advanced_controls'))
        with patch.object(backend.windows, 'apply_vendor') as apply:
            for rule in rules:
                with self.subTest(action=rule.action):
                    self.assertTrue(self.page.blocker.action_allowed(rule.action))
                    switch = self.page.rows[item.key].rule_rows[rule.id][1]
                    self.assertTrue(switch.enabled)
                    switch.event_generate('<Button-1>', x=20, y=9)
                    self.window.update()
                    self.assertTrue(self.page.blocker.rule_enabled(rule))
                    self.assertFalse(hasattr(self.page, '_rule_window'))
                    switch.invoke()
                    self.assertFalse(self.page.blocker.rule_enabled(rule))
            apply.assert_not_called()
        self.mocks[5].assert_not_called()

    def test_ldplayer9_group_and_child_switches_work_after_index_binding(self):
        record=backend.inventory.index_record(
            dict(DisplayName='雷电模拟器9',UninstallString=f'"{self.base / "dnuninst.exe"}"'),
            dict(hive='HKLM',view=32,subkey='leidian9'))
        definition=next(g for g in backend.catalog() if g.definition['name']=='dnplayer9')
        item=backend.huorong.discover(definition,[record],backend._roots)
        self.page.items=[item];self.page.blocker.set_items([item]);self.page.render()
        self.page.toggle_expanded(item.key);self.window.update()
        row=self.page.rows[item.key]
        with patch.object(backend.windows,'apply_vendor') as apply:
            self.assertTrue(row.switch.enabled)
            row.switch.event_generate('<Button-1>',x=20,y=9);self.window.update()
            self.assertTrue(self.page.blocker.group_enabled(item))
            self.assertEqual(self.page.blocker.selected_count(),6)
            child=row.rule_rows[item.rules[0].id][1]
            self.assertTrue(child.enabled)
            child.event_generate('<Button-1>',x=20,y=9);self.window.update()
            self.assertEqual(self.page.blocker.selected_count(),5)
            row.switch.invoke();self.window.update()
            self.assertEqual(self.page.blocker.selected_count(),0)
            apply.assert_not_called()
        self.mocks[5].assert_not_called()

    def test_unsupported_rules_and_empty_groups_are_hidden_and_not_counted(self):
        rule = replace(vendor_rule(), id='cannot-enable', supported=False, reason='尺寸范围上下界颠倒', matcher=None)
        item = backend.Software('unsupported-click', '测试软件', (r'C:\Apps\Demo',), (rule,), source='huorong')
        self.page.items = [item]
        self.page.blocker.set_items([item])
        self.page.render()
        self.assertEqual(self.page.items,[])
        self.assertEqual(self.page.rows,{})
        self.assertEqual(self.page.blocker.items,[])
        self.assertEqual(self.window.nav_metrics['popup']['text'],'0项')
        self.assertFalse(hasattr(self.page,'catalog_status_label'))
        self.assertFalse(self.page.blocker.rule_enabled(rule))

    def test_direct_vendor_switch_save_failure_can_retry(self):
        rule = vendor_rule(action='hide', identity='retry-direct')
        item = backend.Software('retry-click', '测试软件', (r'C:\Apps\Demo',), (rule,), source='huorong')
        self.page.items = [item]
        self.page.blocker.set_items([item])
        self.page.render()
        switch = self.page.rows[item.key].rule_rows[rule.id][1]
        with patch.object(backend, '_atomic', side_effect=OSError('fixture denied')):
            switch.invoke()
        self.assertIn('fixture denied', self.page.note.get())
        self.assertFalse(self.page.blocker.rule_enabled(rule))
        switch.invoke()
        self.assertTrue(self.page.blocker.rule_enabled(rule))

    def test_vendor_icon_bytes_display_software_image(self):
        png = (core.APP_DIR/'assets/icon-qq.png').read_bytes()
        item = backend.Software('vendor-icon', 'QQ', (r'C:\Apps\Demo',), (vendor_rule(),), icon=png, source='huorong')
        self.page.items=[item];self.page.blocker.set_items([item]);self.page.render()
        self.window.update()
        image = self.page.icons[item.key]
        self.assertIsNot(image, self.window.images.navigation('popup'))
        self.assertEqual((image.width(),image.height()),(32,32))
        self.assertEqual(self.page.rows[item.key].icon.cget('image'),str(image))

# Purpose: Regression tests for popup groups, expansion, switches and history
