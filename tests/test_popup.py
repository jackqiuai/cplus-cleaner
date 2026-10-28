"""Backend fixtures only; window messages, observations and icons are mocked."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from cplus_cleaner.common import core
from cplus_cleaner.features.popup import popup
from tests.popup_fixtures import definition as fixture_definition


class PopupTests(unittest.TestCase):
    def test_stopping_or_changing_rules_invalidates_in_flight_window_observations(self):
        for action in ('stop', 'disable'):
            with self.subTest(action=action):
                blocker = self.blocker(active=True)
                entered, release = threading.Event(), threading.Event()
                results = []
                def slow_snapshot():
                    entered.set()
                    release.wait(3)
                    return [self.window]
                self.snapshot.side_effect = slow_snapshot
                self.close.reset_mock()
                worker = threading.Thread(target=lambda: results.append(blocker.poll()))
                worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    if action == 'stop':
                        blocker.stop()
                    else:
                        blocker.set_rule(self.rule, False)
                finally:
                    release.set()
                    worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(results, [False])
                self.close.assert_not_called()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='popup-fixture-', dir=core.APP_DIR.parent)
        self.base = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.data = self.base/'state'
        self.patch(core, 'DATA_DIR', new=self.data)
        self.clock = self.patch(popup.time, 'time', return_value=2_000_000)
        self.snapshot = self.patch(popup.windows, 'snapshot', return_value=[])
        self.close = self.patch(popup.windows, 'apply_vendor', return_value=True)
        self.patch(popup.windows, 'enrich', side_effect=lambda item, points=(): dict(item))
        self.gone = self.patch(popup.windows, 'disappeared', return_value=False)
        self.root = self.base/'software'
        self.root.mkdir()
        self.image = self.root/'advert.exe'
        self.image.write_bytes(b'fixture executable never executed')
        scan = dict(procname=r'%INSTDIR%\advert.exe', caption='今日推荐',
                    **{'class':'PopupClass'},size='333,111',pos=5,styles=0x80000000,exstyles=8)
        rule = popup.Rule('a','今日推荐','PopupClass','advert.exe',333,111,5,True,'huorong','close','close',
                          definition=dict(name='a',scan=scan,treat='close'))
        self.rule = popup.huorong.bind_rule(rule,(str(self.root),))
        self.other = popup.huorong.bind_rule(replace(rule,id='b',title='更新提示',window_class='UpdatePopup',
            definition=dict(name='b',scan=dict(scan,caption='更新提示',**{'class':'UpdatePopup'}),treat='close')),(str(self.root),))
        definition=dict(name='fixture-demo',exist=dict(appname='*示例软件*'),instdir=dict(appname='*示例软件*'))
        self.software = popup.Software('demo','示例软件',(str(self.root),),(self.rule,self.other),source='huorong',definition=definition)
        self.window = dict(hwnd=0x1234,pid=123456,created=987654321,image=str(self.image),popup=True,
                           title=self.rule.title,**{'class':self.rule.window_class},width=333,height=111,
                           logical_width=333,logical_height=111,styles=0x80000000,exstyles=8,position=5)

    def patch(self, target, name, **kwargs):
        context = patch.object(target, name, **kwargs)
        value = context.start()
        self.addCleanup(context.stop)
        return value

    def blocker(self, active=False, software=None):
        blocker = popup.Blocker()
        blocker.set_items([software or self.software])
        if active:
            blocker.set_all(True)
        return blocker

    def observe(self, rows, gone=False, now=None):
        self.snapshot.return_value = rows
        self.gone.return_value = gone
        if now is not None:
            self.clock.return_value = now

    def test_catalog_contains_only_all_200_groups_769_unique_vendor_definitions(self):
        definitions = popup.catalog()
        self.assertEqual(len(definitions),200)
        rules=[rule for group in definitions for rule in group.rules]
        self.assertEqual(len(rules),769)
        self.assertEqual(len({rule.id for rule in rules}),769)
        self.assertTrue(all(group.source=='huorong' for group in definitions))
        self.assertTrue(all(rule.source=='huorong' for rule in rules))
        self.assertFalse((core.APP_DIR/'assets/popup-rules.json').exists())

    def test_system_msi_uninstaller_is_never_admitted_as_application_root(self):
        system = self.base/'Windows'
        installer = system/'System32'/'msiexec.exe'
        installer.parent.mkdir(parents=True)
        installer.write_bytes(b'fixture, never executed')
        record = dict(InstallLocation=str(self.root), UninstallString=f'"{installer}"', _native_icon=installer)
        with patch.dict(popup.os.environ, {'SystemRoot': str(system)}):
            roots = popup._roots(record)
        self.assertTrue(popup.owned(str(self.image), roots))
        self.assertFalse(popup.owned(str(installer), roots))
        self.assertNotIn(str(installer.parent), roots)

    def test_scan_counts_installed_rules_not_windows_and_avoids_directory_size_walk(self):
        definitions = [fixture_definition(name,key,count) for name,key,count in
                       [('迅雷','scan-thunder',15),('酷狗音乐','scan-music',2),('WinRAR','scan-archive',1)]]
        self.patch(popup,'catalog',return_value=definitions)
        records = []
        for index, name in enumerate(('迅雷 12', '酷狗音乐', 'WinRAR 7.0 (64-bit)')):
            root = self.base/f'installed-{index}'
            root.mkdir()
            exe = root/'uninstall.exe'
            exe.write_bytes(b'fixture')
            records.append(dict(DisplayName=name, InstallLocation=str(root),
                                UninstallString=f'"{exe}"', _native_icon=str(exe)))
        records.append(dict(records[0], DisplayName='迅雷（另一注册表视图）'))
        registry = self.patch(popup.inventory, 'registry_records', return_value=(records, []))
        icon = self.patch(popup.software_windows, 'extract_icon', return_value=b'icon')
        size = self.patch(popup.installed, '_directory_size', side_effect=AssertionError('must not size'))
        walk = self.patch(popup.os, 'walk', side_effect=AssertionError('must not recurse'))
        progress, cancel = Mock(), threading.Event()
        result = popup.scan(cancel, progress)
        self.assertFalse(result['errors'])
        self.assertFalse(result['cancelled'])
        self.assertEqual([item.name for item in result['items']], ['迅雷', '酷狗音乐', 'WinRAR'])
        self.assertEqual(sum(len(item.rules) for item in result['items']), 18)
        self.assertTrue(all(item.icon == b'icon' for item in result['items']))
        self.assertEqual(len(result['items'][0].roots), 1)
        registry.assert_called_once_with(cancel)
        self.assertEqual(icon.call_count, 3)
        self.assertEqual(progress.call_count, 3)
        self.assertIn('18', progress.call_args.args[0])
        size.assert_not_called()
        walk.assert_not_called()
        self.snapshot.assert_not_called()
        self.close.assert_not_called()
        self.assertFalse(self.data.exists())

    def test_scan_casefolds_literal_software_names_merges_roots_without_regex(self):
        other = self.base/'another-install'
        other.mkdir()
        self.patch(popup, 'catalog', return_value=[fixture_definition('Demo [Pro]+','literal-name',2)])
        self.patch(popup.inventory, 'registry_records', return_value=([
            dict(DisplayName='DEMO [pRO]+ Edition', InstallLocation=str(self.root)),
            dict(DisplayName='Demo [Pro]+ Second entry', InstallLocation=str(other)),
            dict(DisplayName='Demo PProooo Edition', InstallLocation=str(self.base/'missing')),
        ], []))
        self.patch(popup.software_windows, 'extract_icon', return_value=None)
        result = popup.scan()
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(set(result['items'][0].roots), {str(self.root), str(other)})
        self.assertEqual(len(result['items'][0].rules),2)
        self.assertFalse(result['errors'])

    def test_scan_progress_checks_every_catalog_group_including_uninstalled_software(self):
        self.patch(popup.inventory, 'registry_records', return_value=([], []))
        self.patch(popup.huorong,'discover',return_value=None)
        updates = []
        result = popup.scan(stage_progress=updates.append)
        self.assertIsNone(updates[0]['total'])
        checks = updates[1:]
        self.assertEqual([item['completed'] for item in checks], list(range(201)))
        self.assertTrue(all(item['total'] == 200 for item in checks))
        self.assertEqual(result['items'], [])
        self.assertFalse(result['cancelled'])

    def test_cancelled_scan_progress_stops_at_actual_completed_group(self):
        self.patch(popup.inventory, 'registry_records', return_value=([], []))
        self.patch(popup.huorong,'discover',return_value=None)
        cancel, updates = threading.Event(), []
        def report(data):
            updates.append(data)
            if data.get('completed') == 5:
                cancel.set()
        result = popup.scan(cancel, stage_progress=report)
        self.assertTrue(result['cancelled'])
        self.assertEqual(updates[-1]['completed'], 5)
        self.assertEqual(updates[-1]['total'], 200)

    def test_scan_preserves_registry_errors_hides_missing_root_and_honors_cancel(self):
        self.patch(popup, 'catalog', return_value=[self.software])
        self.patch(popup.inventory, 'registry_records', return_value=([
            dict(DisplayName=self.software.name, InstallLocation=str(self.base/'missing')),
        ], ['Registry denied']))
        self.patch(popup.software_windows, 'extract_icon', return_value=None)
        result = popup.scan()
        self.assertEqual(result['items'], [])
        self.assertIn('Registry denied', result['errors'])
        cancel = threading.Event()
        cancel.set()
        result = popup.scan(cancel)
        self.assertTrue(result['cancelled'])
        self.assertFalse(result['items'])

    def test_vendor_match_requires_scope_complete_title_class_size_and_process(self):
        self.assertTrue(popup.matches(self.rule,self.window,self.software))
        for change in ({'title':'different'},{'class':'OtherClass'},{'logical_width':334},{'logical_height':112},
                       {'image':str(self.root/'unrelated.exe')},{'styles':0},{'position':3}):
            with self.subTest(change=change):
                self.assertFalse(popup.matches(self.rule,dict(self.window,**change),self.software))
        legacy=replace(self.rule,source='legacy')
        self.assertFalse(popup.matches(legacy,self.window,self.software))
        blocker=self.blocker()
        blocker.set_items([replace(self.software,source='legacy')])
        self.assertEqual(blocker.items,[])
        blocker.state['enabled']=True
        blocker.state['rules'][legacy.id]=True
        self.observe([self.window]);blocker.poll()
        self.close.assert_not_called()

    def test_vendor_style_fields_are_required_without_legacy_timer_fallback(self):
        self.assertFalse(popup.matches(self.rule,dict(self.window,styles=0),self.software))
        missing={key:value for key,value in self.window.items() if key!='styles'}
        self.assertFalse(popup.matches(self.rule,missing,self.software))

    def test_window_ownership_is_case_insensitive_and_bounded_to_absolute_roots(self):
        roots=(r'D:\Applications\Demo',)
        self.assertTrue(popup.owned(r'd:\APPLICATIONS\DEMO\bin\advert.exe',roots))
        for image in (r'D:\Applications\Demo-other\advert.exe',r'E:\Applications\Demo\advert.exe',
                      r'D:\Applications\Demo\..\Other\advert.exe',r'advert.exe',''):
            self.assertFalse(popup.owned(image,roots))
        for invalid in ((),('D:\\',),(r'relative\Demo',)):
            self.assertFalse(popup.owned(r'D:\Applications\Demo\advert.exe',invalid))

    def test_global_defaults_off_rules_selected_without_observing_windows(self):
        blocker = self.blocker()
        self.assertFalse(blocker.enabled)
        self.assertTrue(blocker.group_enabled(self.software))
        self.assertTrue(blocker.rule_enabled(self.rule))
        self.assertEqual(blocker.selected_count(), 2)
        self.assertEqual(blocker.total_count, 0)
        self.assertFalse(blocker.poll())
        self.snapshot.assert_not_called()
        self.close.assert_not_called()
        self.assertFalse(self.data.exists())

    def test_group_switch_changes_children_and_child_switch_updates_parent(self):
        blocker = self.blocker()
        blocker.set_software(self.software, False)
        self.assertEqual(blocker.selected_count(), 0)
        self.assertFalse(blocker.group_enabled(self.software))
        self.assertFalse(blocker.rule_enabled(self.rule))
        self.assertFalse(blocker.rule_enabled(self.other))
        blocker.set_rule(self.rule, True)
        self.assertTrue(blocker.enabled)
        self.assertTrue(blocker.group_enabled(self.software))
        self.assertTrue(blocker.rule_enabled(self.rule))
        self.assertFalse(blocker.rule_enabled(self.other))
        self.assertEqual(blocker.selected_count(), 1)
        blocker.set_rule(self.rule, False)
        self.assertFalse(blocker.group_enabled(self.software))
        blocker.set_software(self.software, True)
        self.assertEqual(blocker.selected_count(), 2)
        blocker.set_all(False)
        self.assertFalse(blocker.enabled)
        self.assertEqual(blocker.selected_count(), 0)

    def test_failed_settings_save_keeps_memory_and_existing_file_unchanged(self):
        blocker = self.blocker(True)
        before = deepcopy(blocker.state)
        file = self.data/'popup'/'state.json'
        disk = file.read_bytes()
        with patch.object(popup, '_atomic', side_effect=OSError('disk denied')):
            for operation in (lambda: blocker.set_all(False),
                              lambda: blocker.set_software(self.software, False),
                              lambda: blocker.set_rule(self.rule, False), blocker.clear_history):
                with self.subTest(operation=operation):
                    with self.assertRaises(OSError):
                        operation()
                    self.assertEqual(blocker.state, before)
                    self.assertEqual(file.read_bytes(), disk)

    def test_restart_restores_switches_counts_history_but_not_pending(self):
        blocker = self.blocker(True)
        blocker.set_rule(self.other, False)
        self.observe([self.window])
        blocker.poll()
        self.observe([], gone=True, now=2_000_001)
        self.assertTrue(blocker.poll())
        restored = self.blocker()
        self.assertTrue(restored.enabled)
        self.assertTrue(restored.group_enabled(self.software))
        self.assertTrue(restored.rule_enabled(self.rule))
        self.assertFalse(restored.rule_enabled(self.other))
        self.assertEqual(restored.rule_count(self.rule), 1)
        self.assertEqual(restored.history(), blocker.history())
        self.assertFalse(restored.pending)

    def test_close_request_only_pending_and_not_resent_or_counted_while_visible(self):
        blocker = self.blocker(True)
        self.observe([self.window])
        self.assertFalse(blocker.poll())
        self.assertEqual(blocker.total_count, 0)
        self.assertEqual(blocker.history(), [])
        self.assertEqual(len(blocker.pending), 1)
        self.close.assert_called_once()
        self.assertEqual(self.close.call_args.args[0],self.window)
        self.observe([dict(self.window)], now=2_000_010)
        self.assertFalse(blocker.poll())
        self.close.assert_called_once()
        self.assertEqual(blocker.total_count, 0)
        self.gone.assert_not_called()

    def test_snapshot_omission_with_unknown_disappearance_cannot_count_or_resend(self):
        blocker = self.blocker(True)
        self.observe([self.window])
        blocker.poll()
        self.observe([], now=2_000_001)
        self.assertFalse(blocker.poll())
        self.gone.assert_called_once_with(self.window)
        self.assertEqual(len(blocker.pending), 1)
        self.assertEqual(blocker.total_count, 0)
        self.assertEqual(blocker.history(), [])
        self.observe([self.window], now=2_000_002)
        blocker.poll()
        self.close.assert_called_once()

    def test_confirmed_disappearance_counts_once_with_latest_record_per_rule(self):
        blocker = self.blocker(True)
        self.observe([self.window])
        blocker.poll()
        self.observe([], gone=True, now=2_000_001)
        self.assertTrue(blocker.poll())
        self.assertEqual(blocker.total_count, 1)
        self.assertEqual(blocker.rule_count(self.rule), 1)
        self.assertEqual(blocker.group_count(self.software), 1)
        self.assertFalse(blocker.pending)
        row = blocker.history()[0]
        self.assertEqual((row['software'], row['title'], row['count'], row['rule_id']),
                         (self.software.name, self.rule.label, 1, self.rule.id))
        self.assertFalse(blocker.poll())
        self.assertEqual(blocker.total_count, 1)
        self.observe([dict(self.window, hwnd=0x4321)], now=2_000_002)
        blocker.poll()
        self.observe([], gone=True, now=2_000_003)
        self.assertTrue(blocker.poll())
        self.assertEqual(blocker.total_count, 2)
        self.assertEqual(len(blocker.history()), 1)
        self.assertEqual(blocker.history()[0]['count'], 2)
        self.assertEqual(blocker.history()[0]['time'], 2_000_003)

    def test_rejected_close_is_never_pending_and_never_counts(self):
        blocker = self.blocker(True)
        self.close.return_value = False
        self.observe([self.window])
        self.assertFalse(blocker.poll())
        self.assertFalse(blocker.pending)
        self.observe([], gone=True)
        self.assertFalse(blocker.poll())
        self.assertEqual(blocker.total_count, 0)
        self.assertEqual(blocker.history(), [])
        self.gone.assert_not_called()

    def test_confirmed_count_save_failure_retries_without_losing_or_duplicating_event(self):
        blocker = self.blocker(True)
        self.observe([self.window])
        blocker.poll()
        self.observe([], gone=True)
        with patch.object(popup, '_atomic', side_effect=OSError('fixture denied')):
            with self.assertRaises(OSError):
                blocker.poll()
        self.assertEqual(blocker.rule_count(self.rule), 1)
        self.assertTrue(blocker.poll(), 'A successful retry must refresh the UI counter')
        restored = self.blocker()
        self.assertEqual(restored.rule_count(self.rule), 1)
        self.assertEqual(len(restored.history()), 1)

    def test_inaccessible_metadata_expires_without_counting(self):
        blocker = self.blocker(True)
        self.observe([self.window])
        blocker.poll()
        self.observe([], now=2_000_016)
        self.assertFalse(blocker.poll())
        self.assertFalse(blocker.pending)
        self.assertEqual(blocker.total_count, 0)
        self.assertEqual(blocker.history(), [])

    def test_duplicate_occurrences_do_not_double_act_or_count_one_window(self):
        duplicate = replace(self.rule, id='original-duplicate')
        blocker = self.blocker(True, replace(self.software, rules=(self.rule, duplicate)))
        self.assertEqual(blocker.selected_count(), 2)
        self.observe([self.window, dict(self.window)])
        blocker.poll()
        self.close.assert_called_once()
        self.assertEqual(len(blocker.pending), 1)
        self.observe([], gone=True, now=2_000_001)
        blocker.poll()
        self.assertEqual(blocker.total_count, 1)
        self.assertEqual(blocker.rule_count(self.rule), 1)
        self.assertEqual(blocker.rule_count(duplicate), 0)

    def test_disabled_rule_or_group_does_not_close_matching_window(self):
        blocker = self.blocker(True)
        self.observe([self.window])
        blocker.set_rule(self.rule, False)
        blocker.poll()
        self.close.assert_not_called()
        blocker.set_software(self.software, False)
        blocker.poll()
        self.close.assert_not_called()
        self.assertEqual(blocker.total_count, 0)

    def test_history_is_current_rules_last_30_days_and_clear_preserves_counts(self):
        blocker = self.blocker()
        now = 5_000_000
        self.clock.return_value = now
        cutoff = now-popup.HISTORY_DAYS*86400
        def update(state):
            state['counts'] = {self.rule.id: 9, self.other.id: 6, 'old-rule': 12}
            state['history'] = {
                self.rule.id: dict(time=now-30, title='当前弹窗', count=1),
                self.other.id: dict(time=cutoff, title='刚好30天', count=2),
                'old-rule': dict(time=now-1, title='已移除规则', count=12),
            }
        blocker._commit(update)
        self.assertEqual([row['title'] for row in blocker.history()], ['当前弹窗', '刚好30天'])
        self.assertEqual([row['count'] for row in blocker.history()], [9, 6])
        self.clock.return_value = now+1
        self.assertEqual([row['title'] for row in blocker.history()], ['当前弹窗'])
        blocker.clear_history()
        self.assertEqual(blocker.history(), [])
        self.assertEqual(blocker.total_count, 27)
        restored = self.blocker()
        self.assertEqual(restored.total_count, 27)
        self.assertEqual(restored.history(), [])


if __name__ == '__main__':
    unittest.main()

# Purpose: Regression tests for popup scan and blocking orchestration
