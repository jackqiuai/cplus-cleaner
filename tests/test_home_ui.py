"""Real Tk homepage workflows; all operation boundaries stay disabled."""
from contextlib import ExitStack
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
from cplus_cleaner.features.home import home_scan
from cplus_cleaner.features.home import home_results
from cplus_cleaner.features.home import home_ui
from cplus_cleaner.localization import i18n
from cplus_cleaner.features.large_files import large_files
from cplus_cleaner.features.popup import popup
from cplus_cleaner.application import preferences
from cplus_cleaner.features.privacy import privacy
from cplus_cleaner.features.app_compression import software_compression
from cplus_cleaner.features.app_mover import software_migration
from cplus_cleaner.features.uninstall import software_uninstall
from tests.popup_fixtures import groups as fixture_groups


def fixture_result(root):
    """Illustrative snapshots for layout/handoff, never deletion candidates."""
    gib = 1024**3
    results = {}
    for key, name, size in (("cdrive", "临时文件 / Windows日志", 18*gib),
                            ("chat", "微信 / 聊天图片", 507*1024**2)):
        folder = root/key
        folder.mkdir(exist_ok=True)
        rule = core.Rule(name, folder, name.split(" / ")[0], days=0)
        item = core.Candidate(str(folder/'example.dat'), str(folder), name, size, 1, 1, 1)
        entries = None if key == 'cdrive' else [dict(index=0, group=rule.group, name='聊天图片')]
        payload = dict(rules=[rule], items=[item], system_items=[], manifest_items=entries,
                       diagnostics=[], folder=str(folder))
        if key == 'chat':
            payload.update(apps=[dict(id='wechat', name='微信', status='data')], defaults=set(), errors=[])
        results[key] = dict(key=key, value=size, unit='bytes', payload=payload,
                            cancelled=False, error='', diagnostics=[], message='扫描完成')
    session = privacy.Session()
    session.scanned = True
    session.categories['run'].records = [dict(id=f'example-{i}') for i in range(12)]
    move = software_migration.Software('示例软件', str(root/'program'), 34*gib, 1, 3, 1, 1)
    compact = software_compression.Software('example', '示例软件', str(root/'program'), 56*gib)
    groups = fixture_groups(root)
    payloads = dict(
        large=dict(items=[core.Candidate(str(root/'example.exe'), str(root), '安装包', 7*gib, 1, 1, 1)],
                   counts={}, cancelled=False, root=str(root), minimum=large_files.MINIMUM, blocked_paths={}),
        privacy=session,
        compress=dict(items=[compact], errors=[], skipped=0, cancelled=False),
        popup=dict(items=groups, errors=[], cancelled=False),
        migrate=dict(items=[move], skipped={}, errors=[], cancelled=False),
        uninstall=dict(items=[software_uninstall.Software('example-uninstall', '示例软件', 12*gib,
                       '20261024', str(root/'installed'), 'never-run')], errors=[], cancelled=False))
    for key in ('large', 'privacy', 'compress', 'popup', 'migrate', 'uninstall'):
        value = 12 if key == 'privacy' else 18 if key == 'popup' else len(payloads[key]['items']) if key == 'uninstall' else sum(i.size for i in payloads[key]['items'])
        results[key] = dict(key=key, value=value, unit=home_scan.UNITS[key], payload=payloads[key],
                            cancelled=False, error='', diagnostics=[], message='扫描完成')
    ordered = {key: results[key] for key in home_scan.FEATURES}
    return dict(results=ordered, cancelled=False, cleanup_bytes=home_scan.cleanup_bytes(ordered))


class HomeUITests(unittest.TestCase):
    def setUp(self):
        # Baseline captions must not depend on the runner's Windows language.
        language = patch.object(desktop.i18n, 'detect_language', return_value='zh')
        language.start()
        self.addCleanup(language.stop)
        self.gc_enabled = gc.isenabled()
        gc.disable()
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix='home-ui-', dir=core.APP_DIR.parent)
        self.root = Path(self.temp.name)
        self.bundle = fixture_result(self.root)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(core, 'DATA_DIR', self.root/'state'))
        self.stack.enter_context(patch.object(preferences, 'load', return_value=dict(preferences.DEFAULTS)))
        self.stack.enter_context(patch.object(preferences, 'read_autostart', return_value=False))
        self.stack.enter_context(patch.object(desktop, 'disk_snapshot', return_value=dict(
            root='C:\\', total=180*1024**3, used=140*1024**3, free=40*1024**3)))
        self.stack.enter_context(patch.object(large_files, 'disk_inventory', return_value=[]))
        self.stack.enter_context(patch.object(large_files, 'default_destination', return_value='D:\\bigfiles'))
        self.stack.enter_context(patch.object(software_migration, 'default_destination', return_value='D:\\Program Files'))
        self.scan_mock = self.stack.enter_context(patch.object(home_scan, 'scan', side_effect=self.scan_fixture))
        self.error_mock = self.stack.enter_context(patch.object(desktop.messagebox, 'showerror'))
        self.observations = self.stack.enter_context(patch.object(popup.windows,'snapshot',return_value=[]))
        self.actions = [self.stack.enter_context(patch.object(module, name, side_effect=AssertionError('read-only UI test')))
                        for module, name in ((core, 'delete_files'), (core, 'move_file'),
                            (desktop.cdrive_system, 'run_action'), (software_migration, 'move'),
                            (software_compression, 'compress'),
                            (popup.windows, 'post_close'), (software_uninstall, 'uninstall'),
                            (software_uninstall, 'scan'))]
        self.window = desktop.DesktopApplication()
        self.window.geometry('980x650+20+20')
        self.window.attributes('-topmost', True)
        self.errors = []
        self.window.report_callback_exception = lambda kind, error, trace: self.errors.append(str(error))
        self.page = self.window.home_page
        self.pump(lambda: self.page.winfo_width() > 700)

    def tearDown(self):
        self.window.cancel.set()
        self.pump(lambda: not self.window.busy)
        for timer in self.window.tk.call('after', 'info'):
            self.window.tk.call('after', 'cancel', timer)
        self.window.destroy()
        self.assertEqual(self.errors, [])
        self.error_mock.assert_not_called()
        for action in self.actions:
            action.assert_not_called()
        self.stack.close()
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
        self.assertTrue(condition(), 'homepage UI timed out')

    def scan_fixture(self, cancel, progress, *, folders=None, enabled=None):
        keys = [key for key in home_scan.FEATURES if key in enabled]
        for key in keys:
            progress(dict(key=key, state='waiting', message='等待扫描'))
        for key in keys:
            progress(dict(key=key, state='running', completed=None, total=None, message='正在读取信息'))
            feature = self.bundle['results'][key]
            progress(dict(key=key, state='done', completed=1, total=1, value=feature['value'], message='扫描完成'))
        result = {key: self.bundle['results'][key] for key in keys}
        return dict(results=result, cancelled=False, cleanup_bytes=home_scan.cleanup_bytes(result))

    def test_default_cards_background_assets_and_small_window_layout(self):
        self.assertEqual(list(self.page.tiles), list(home_scan.FEATURES))
        self.assertEqual(len(self.page.tiles), 8)
        self.assertGreater(self.page.tiles['uninstall'].winfo_x(), self.page.tiles['migrate'].winfo_x())
        self.assertEqual(self.page.tiles['uninstall'].winfo_y(), self.page.tiles['migrate'].winfo_y())
        self.assertTrue(all(tile.selected for tile in self.page.tiles.values()))
        self.assertTrue(self.page.canvas.find_withtag("core-art"))
        self.assertIsNotNone(self.window.images.get("brand/sidebar.png"))
        self.assertIsNotNone(self.page.background)
        self.assertFalse(self.window.footer.winfo_ismapped())
        for tile in self.page.tiles.values():
            self.assertTrue(tile.winfo_ismapped())
            self.assertGreaterEqual(tile.winfo_x(), 0)
            self.assertLessEqual(tile.winfo_x()+tile.winfo_width(), self.page.winfo_width())
        self.assertTrue(self.page.scan_button.winfo_ismapped())
        self.assertLess(self.page.scan_button.winfo_y()+44, self.page.disk.winfo_y())
        self.scan_mock.assert_not_called()

    def test_scan_stays_home_updates_cards_and_hands_all_results_over_without_rescan(self):
        self.page.scan_button.invoke()
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.window.current_page, 'home')
        self.assertEqual(self.page.scan_button.caption, '立即处理')
        self.assertTrue(self.page.canvas.find_withtag('core-complete'))
        self.assertTrue(all(tile.state == 'done' for tile in self.page.tiles.values()))
        self.assertIn(desktop.size_text(self.bundle['cleanup_bytes']), self.page.heading.get())
        self.assertEqual(self.window.nav_metrics['popup']['value'], 18)
        self.assertEqual(self.window.nav_metrics['uninstall']['text'], '1款')
        tile = self.page.tiles['uninstall']
        self.assertEqual([tile.itemcget(item, 'text') for item in tile.find_withtag('metric')], ['1款'])
        self.assertEqual(self.window.nav_metrics['privacy']['unit'], '条')
        self.assertEqual(self.page.canvas.find_withtag('scan-orbit'), ())
        for key in home_scan.FEATURES:
            self.page.open_feature(key)
            self.window.update()
            self.assertEqual(self.window.current_page, key)
            page = self.window.views[key]
            if key in ('cdrive', 'chat'):
                self.assertIsNotNone(page.session)
                self.assertEqual(page.session.total_bytes, self.bundle['results'][key]['value'])
            elif key == 'privacy':
                self.assertIs(page.session, self.bundle['results'][key]['payload'])
            elif key == 'popup':
                self.assertTrue(page.blocker.enabled)
                self.assertEqual(len(page.items), 3)
            elif key == 'uninstall':
                self.assertTrue(page._loaded)
                self.assertEqual(len(page.apps), 1)
                self.assertEqual(page.selected, set())
                self.assertFalse(page.uninstall_button.enabled)
            self.assertFalse(self.window.busy)
            self.window.navigate('home')
        self.scan_mock.assert_called_once()
        self.page.scan_button.invoke()
        self.assertEqual(self.window.current_page, 'cdrive')
        page = self.window.cdrive_page
        session = page.session
        page.select_all(False)
        self.window.navigate('home')
        self.page.open_feature('cdrive')
        self.assertIs(page.session, session)
        self.assertEqual(page.session.selected_bytes, 0)

    def assert_tile_summary_clear(self, tile):
        action = tile.bbox('action')
        self.assertIsNotNone(action)
        for tag in ('detail', 'metric'):
            bounds = tile.bbox(tag)
            if bounds:
                self.assertLessEqual(bounds[2]+8, action[0], (tile.key, tile.state, tag, bounds, action))
                self.assertGreaterEqual(bounds[0], 0)
                self.assertLessEqual(bounds[3], tile.winfo_height())

    def test_english_scan_summaries_keep_links_clear(self):
        previous_language = i18n.language()
        release = threading.Event()
        self.addCleanup(release.set)
        try:
            i18n.set_language('en')
            self.window.geometry('1120x700+20+20')
            self.window.update()

            def paused(cancel, progress, **kwargs):
                for key in home_scan.FEATURES:
                    progress(dict(key=key, state='waiting'))
                progress(dict(key='cdrive', state='running', message='正在读取信息'))
                release.wait(8)
                return self.scan_fixture(cancel, progress, **kwargs)

            self.scan_mock.side_effect = paused
            self.page.scan_button.invoke()
            tile = self.page.tiles['cdrive']
            self.pump(lambda: tile.state == 'running')
            self.assertEqual(tile.itemcget('action', 'text'), 'Scanning')
            for card in self.page.tiles.values():
                self.assert_tile_summary_clear(card)
            release.set()
            self.pump(lambda: not self.window.busy)
            self.assertEqual(tile.itemcget('action', 'text'), 'Open ›')
            self.assertEqual(tile.itemcget('metric', 'text'), '18GB')
            for card in self.page.tiles.values():
                self.assert_tile_summary_clear(card)
            x1, y1, x2, y2 = tile.bbox('action')
            with patch.object(self.page, 'open_feature') as open_feature:
                tile.event_generate('<Button-1>', x=(x1+x2)//2, y=(y1+y2)//2)
                self.window.update()
                open_feature.assert_called_once_with('cdrive')
        finally:
            release.set()
            self.pump(lambda: not self.window.busy)
            i18n.set_language(previous_language)

    def test_summary_fitting_handles_states_widths_and_language_switches(self):
        previous_language = i18n.language()
        try:
            for language in ('en', 'zh', 'en'):
                i18n.set_language(language)
                self.window.update()
                for width in (160, 192, 260):
                    for key in self.page.tiles:
                        self.page.canvas.itemconfigure(f'tile-{key}', width=width)
                    self.window.update()
                    for state in ('idle', 'waiting', 'running', 'done', 'cancelled', 'failed'):
                        for key, tile in self.page.tiles.items():
                            with self.subTest(language=language, width=width, state=state, key=key):
                                tile.state, tile.value, tile.message = state, self.bundle['results'][key]['value'], ''
                                tile.completed, tile.total = None, None
                                tile.paint()
                                self.assert_tile_summary_clear(tile)
                                if state == 'running':
                                    tile.completed, tile.total = 10, 10
                                    tile.paint()
                                    self.assert_tile_summary_clear(tile)
                                    self.assertEqual(tile.itemcget('action', 'text'), '100%')
                    tile = self.page.tiles['cdrive']
                    tile.state = 'idle'
                    with patch.dict(home_ui.DESCRIPTIONS, cdrive='Drive cleanup description with more than twenty characters'):
                        tile.paint()
                        self.assertTrue(tile.itemcget('detail', 'text').endswith('…'))
                        self.assert_tile_summary_clear(tile)
                    tile = self.page.tiles['cdrive']
                    tile.state, tile.message = 'done', '未找到目录'
                    tile.paint()
                    self.assert_tile_summary_clear(tile)
        finally:
            i18n.set_language(previous_language)

    def test_real_scan_stage_and_core_motion_then_cancel_preserves_partial_state(self):
        # A previous page's snapshot must not inflate a cancelled new homepage scan.
        self.assertTrue(home_results.apply_result(self.window, 'chat', self.bundle['results']['chat']))
        self.page.refresh()
        release = threading.Event()
        self.addCleanup(release.set)
        def paused(cancel, progress, **kwargs):
            for key in home_scan.FEATURES:
                progress(dict(key=key, state='waiting', message='等待扫描'))
            progress(dict(key='cdrive', state='running', completed=3, total=10, message='已核对3/10项'))
            release.wait(5)
            feature = dict(self.bundle['results']['cdrive'], cancelled=cancel.is_set())
            return dict(results={'cdrive': feature}, cancelled=cancel.is_set(), cleanup_bytes=feature['value'])
        self.scan_mock.side_effect = paused
        self.page.scan_button.invoke()
        self.pump(lambda: self.page.tiles['cdrive'].state == 'running' and bool(self.page.canvas.find_withtag('scan-orbit')))
        self.assertEqual(self.window.current_page, 'home')
        self.assertEqual(self.page.tiles['chat'].state, 'waiting')
        self.assertEqual(self.page.tiles['cdrive'].total, 10)
        before = [self.page.canvas.coords(item) for item in self.page.canvas.find_withtag('scan-orbit')]
        deadline = time.monotonic()+.15
        self.pump(lambda: time.monotonic() >= deadline)
        after = [self.page.canvas.coords(item) for item in self.page.canvas.find_withtag('scan-orbit')]
        self.assertNotEqual(before, after)
        self.page.scan_button.invoke()
        self.assertTrue(self.window.cancel.is_set())
        release.set()
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.page.tiles['cdrive'].state, 'cancelled')
        self.assertEqual(self.page.tiles['chat'].state, 'idle')
        self.assertIn('18GB', self.page.heading.get())
        self.assertNotIn('18.37GB', self.page.heading.get())
        self.assertFalse(self.page.scanning)
        self.assertEqual(self.page.canvas.find_withtag('core-complete'), ())
        self.assertEqual(self.page.canvas.find_withtag('scan-orbit'), ())

    def test_unchecked_features_are_excluded_and_reselecting_changes_summary(self):
        for key in ('chat', 'compress', 'migrate'):
            self.page.tiles[key].toggle()
        self.page.scan()
        self.pump(lambda: not self.window.busy)
        enabled = self.scan_mock.call_args.kwargs['enabled']
        self.assertNotIn('compress', enabled)
        self.assertNotIn('chat', enabled)
        self.assertEqual(self.page.tiles['chat'].state, 'idle')
        self.assertIn('18GB', self.page.heading.get())
        self.page.tiles['cdrive'].toggle()
        self.assertIn('0KB', self.page.heading.get())

    def test_starting_an_operation_invalidates_home_snapshot_before_future_navigation(self):
        self.page.scan()
        self.pump(lambda: not self.window.busy)
        self.page.open_feature('cdrive')
        self.assertIn('cdrive', self.page.results)
        self.window.start('只读示例任务', lambda: None, lambda result: None)
        self.assertNotIn('cdrive', self.page.results)
        self.pump(lambda: not self.window.busy)

    def test_idle_software_cards_enter_without_starting_scan(self):
        for key in ('cdrive', 'migrate', 'compress'):
            page = self.window.ensure_view(key)
            with patch.object(page, 'scan') as scan:
                if key == 'migrate':
                    page._pending_open = page.after(50, lambda: page.scan())
                self.page.open_feature(key)
                deadline = time.monotonic()+.1
                self.pump(lambda: time.monotonic() >= deadline)
                if key == 'cdrive':
                    scan.assert_called_once()
                else:
                    scan.assert_not_called()
                self.assertEqual(self.window.current_page, key)
            self.window.navigate('home')

    def test_home_card_adopts_new_snapshot_over_an_already_loaded_old_page(self):
        old = dict(self.bundle['results']['compress'])
        old['payload'] = dict(old['payload'], items=[replace(old['payload']['items'][0], size=2048)])
        self.assertTrue(home_results.apply_result(self.window, 'compress', old))
        page = self.window.views['compress']
        self.assertEqual(page.apps[0].size, 2048)
        self.page.scan()
        self.pump(lambda: not self.window.busy)
        with patch.object(software_compression, 'scan', side_effect=AssertionError('must reuse new snapshot')) as scan:
            self.page.open_feature('compress')
            self.window.update()
            self.assertIs(self.window.views['compress'], page)
            self.assertEqual(page.apps[0].size, 56*1024**3)
            self.assertEqual(self.window.nav_metrics['compress']['value'], 56*1024**3)
            scan.assert_not_called()
        # Opening an old loaded page during a new scan must not overwrite the
        # final inventory/authorization snapshot when the homepage finishes.
        self.window.navigate('home')
        fresh = self.bundle['results']['compress']
        fresh['payload']['items'] = [replace(fresh['payload']['items'][0], size=57*1024**3)]
        fresh['value'] = 57*1024**3
        ready, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def paused(*args, **kwargs):
            result = self.scan_fixture(*args, **kwargs)
            ready.set()
            release.wait(5)
            return result
        self.scan_mock.side_effect = paused
        self.page.scan()
        self.pump(ready.is_set)
        self.window.navigate('compress')
        self.assertEqual(page.apps[0].size, 56*1024**3)
        release.set()
        self.pump(lambda: not self.window.busy)
        self.assertEqual(self.window.current_page, 'compress')
        self.assertEqual(page.apps[0].size, 57*1024**3)
        self.assertEqual(self.window.nav_metrics['compress']['value'], 57*1024**3)


if __name__ == '__main__':
    unittest.main()

# Purpose: Regression tests for home cards, illustration and scan state
