"""Real chat fixtures; all deletion stays in verified workspace directories."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import core
import chat_cleanup as chat
import home_scan


def fixtures(root):
    bases = {key: root/key for key in ('documents', 'roaming', 'local')}
    for folder in bases.values():
        folder.mkdir(parents=True, exist_ok=True)
    relative = {
        'wechat_cache': ('documents', 'WeChat Files/account/FileStorage/Cache/cache.bin'),
        'wechat_image': ('documents', 'WeChat Files/account/FileStorage/Image/photo.dat'),
        'wechat_db': ('documents', 'WeChat Files/account/Msg/MSG0.db'),
        'cache_db': ('documents', 'WeChat Files/account/FileStorage/Cache/message.db'),
        'qq_image': ('documents', 'Tencent Files/123/nt_qq/nt_data/Pic/picture.jpg'),
        'qq_cache': ('roaming', 'Tencent/QQ/Code Cache/js/cache.bin'),
        'whatsapp': ('local', 'Packages/5319275A.WhatsAppDesktop_example/LocalState/EBWebView/Default/Cache/Cache_Data/data_0'),
        'whatsapp_db': ('local', 'Packages/5319275A.WhatsAppDesktop_example/LocalState/messages.db'),
        'telegram': ('roaming', 'Telegram Desktop/tdata/user_data/media_cache/0/cached.bin'),
        'telegram_auth': ('roaming', 'Telegram Desktop/tdata/key_datas'),
        'discord': ('roaming', 'discord/Cache/Cache_Data/f_1'),
        'slack': ('roaming', 'Slack/GPUCache/data_0'),
        'teams': ('local', 'Packages/MSTeams_example/LocalCache/Microsoft/MSTeams/GPUCache/data_0'),
        'signal': ('roaming', 'Signal/Code Cache/js/cache.bin'),
        'signal_db': ('roaming', 'Signal/sql/db.sqlite'),
        'signal_attachment': ('roaming', 'Signal/attachments.noindex/00/message-image'),
    }
    files = {}
    for index, (name, (base, relative_path)) in enumerate(relative.items()):
        path = bases[base]/relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes([index+1])*(index+2))
        files[name] = path
    return bases, files


class ChatCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='chat-rules-', dir=core.APP_DIR.parent)
        self.root = Path(self.temp.name)
        self.assertTrue(core.inside(self.root.resolve(), core.APP_DIR.parent.resolve()))
        self.bases, self.files = fixtures(self.root)
        self.state = patch.object(core, 'DATA_DIR', self.root/'state')
        self.state.start()

    def tearDown(self):
        self.state.stop()
        self.temp.cleanup()

    def scan(self, **kwargs):
        return chat.scan(paths={}, bases=self.bases, records=[], icons=False, **kwargs)

    def test_all_eight_apps_preserve_account_stores_and_separate_optional_media(self):
        result = self.scan()
        self.assertEqual({state['id'] for state in result['apps']}, set(chat.APPS))
        self.assertTrue(all(state['status'] == 'data' for state in result['apps']))
        included = {item.path for item in result['items']}
        expected = {str(path) for name, path in self.files.items() if name not in
                    ('wechat_db', 'cache_db', 'whatsapp_db', 'telegram_auth', 'signal_db', 'signal_attachment')}
        self.assertEqual(included, expected)
        for entry in result['manifest_items']:
            self.assertEqual(entry['default'], entry['name'] in ('缓存文件', '临时文件'))
        self.assertNotIn('微信 / 聊天图片', result['defaults'])
        self.assertNotIn('Telegram / 媒体缓存', result['defaults'])
        self.assertTrue(all(path.exists() for path in self.files.values()))

    def test_unknown_and_missing_layouts_are_explicit_not_reset_as_cache(self):
        other = self.root/'other'
        unknown = other/'wa'
        unknown.mkdir(parents=True)
        (unknown/'messages.db').write_bytes(b'preserve')
        result = chat.scan(paths={'whatsapp': str(unknown)}, bases={key: other/key for key in self.bases},
                           records=[dict(DisplayName='Signal', DisplayVersion='99')], icons=False)
        states = {state['id']: state for state in result['apps']}
        self.assertEqual(states['whatsapp']['status'], 'unsupported')
        self.assertEqual(states['signal']['status'], 'unsupported')
        self.assertEqual(states['wechat']['status'], 'missing')
        self.assertEqual(result['items'], [])
        self.assertTrue((unknown/'messages.db').exists())

    def test_webapp_installation_is_identified_without_scanning_shared_browser_profiles(self):
        result = chat.scan(paths={}, bases={key: self.root/'empty'/key for key in self.bases}, icons=False,
                           records=[dict(DisplayName='WhatsApp 网页版', DisplayVersion='1.0',
                                         DisplayIcon=str(self.root/'Web Applications/app/icon.ico'))])
        whatsapp = next(state for state in result['apps'] if state['id'] == 'whatsapp')
        self.assertEqual(whatsapp['variant'], 'web')
        self.assertEqual(whatsapp['status'], 'unsupported')
        self.assertEqual(result['items'], [])

    def test_unreadable_cache_is_reported_while_other_apps_continue(self):
        original = os.scandir
        denied = self.files['slack'].parent
        def read(path):
            if Path(path) == denied:
                raise PermissionError('fixture denied')
            return original(path)
        with patch.object(core.os, 'scandir', side_effect=read):
            result = self.scan()
        self.assertEqual(next(state for state in result['apps'] if state['id'] == 'slack')['status'], 'unreadable')
        self.assertNotIn(str(self.files['slack']), {item.path for item in result['items']})
        self.assertIn(str(self.files['signal']), {item.path for item in result['items']})
        self.assertTrue(result['errors'])

    def test_custom_root_multi_account_and_age_filter(self):
        custom = self.root/'relocated'
        for account in ('first', 'second'):
            path = custom/account/'FileStorage/Image/a.dat'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'old')
            os.utime(path, (1, 1))
        new = custom/'second/FileStorage/Image/new.dat'
        new.write_bytes(b'new')
        chat.save_path('wechat', custom)
        self.assertEqual(chat.load_paths()['wechat'], str(custom))
        result = chat.scan(paths=chat.load_paths(), bases=self.bases, records=[], icons=False, days=7)
        photos = [item for item in result['items'] if item.category == '微信 / 聊天图片']
        self.assertEqual(len(photos), 2)
        self.assertTrue(all(item.mtime_ns == 1_000_000_000 for item in photos))
        self.assertEqual(next(state for state in result['apps'] if state['id'] == 'wechat')['accounts'], 2)
        chat.save_path('wechat')
        self.assertEqual(chat.load_paths(), {})

    def test_delete_uses_retained_snapshot_and_preserves_unselected_and_changed_files(self):
        result = self.scan()
        selected = [item for item in result['items'] if item.category in result['defaults']]
        changed = next(item for item in selected if item.category.startswith('Signal'))
        Path(changed.path).write_bytes(b'changed after scan')
        with patch.object(chat.software_windows, 'running_images', return_value=[]):
            cleaned = chat.delete(result, selected)
        self.assertEqual(cleaned['deleted'], len(selected)-1)
        self.assertEqual(len(cleaned['errors']), 1)
        self.assertTrue(self.files['wechat_image'].exists())
        self.assertTrue(self.files['qq_image'].exists())
        self.assertTrue(self.files['signal_db'].exists())
        self.assertTrue(Path(changed.path).exists())
        self.assertFalse(self.files['wechat_cache'].exists())

    def test_running_apps_and_forged_candidates_are_skipped(self):
        result = self.scan()
        item = next(item for item in result['items'] if item.category == '微信 / 缓存文件')
        forged = replace(item, path=str(self.files['wechat_db']))
        with patch.object(chat.software_windows, 'running_images', return_value=[Path('C:/apps/WeChat.exe')]):
            cleaned = chat.delete(result, [item, forged])
        self.assertEqual(cleaned['deleted'], 0)
        self.assertEqual(len(cleaned['errors']), 2)
        self.assertTrue(all(path.exists() for path in self.files.values()))
        with patch.object(chat.software_windows, 'running_images', side_effect=OSError('denied')):
            self.assertEqual(chat.delete(result, [item])['deleted'], 0)

    def test_links_are_excluded_and_overlapping_rules_hardlinks_deduplicate(self):
        target = self.files['wechat_cache']
        duplicate = target.with_name('linked.bin')
        os.link(target, duplicate)
        result = self.scan()
        same = [item for item in result['items'] if (item.device, item.inode) ==
                (target.stat().st_dev, target.stat().st_ino)]
        self.assertEqual(len(same), 1)
        original = core.is_link
        with patch.object(core, 'is_link', side_effect=lambda path: path == target.parent or original(path)):
            result = self.scan()
        self.assertNotIn(str(target), {item.path for item in result['items']})

    def test_cancel_keeps_partial_scan_and_progress_uses_actual_app_counts(self):
        cancel, progress = threading.Event(), []
        def report(data):
            progress.append(data)
            if data.get('completed') == 1:
                cancel.set()
        result = self.scan(cancel=cancel, progress=report)
        self.assertTrue(result['cancelled'])
        self.assertTrue(result['items'])
        self.assertTrue(all(item.category.startswith('微信 /') for item in result['items']))
        self.assertEqual(progress[-1]['completed'], 1)
        self.assertEqual(progress[-1]['total'], 8)

    def test_homepage_deduplicates_shared_cdrive_chat_paths(self):
        result = self.scan()
        shared = result['items'][0]
        total = home_scan.cleanup_bytes(dict(cdrive=dict(payload=dict(items=[shared])), chat=dict(payload=result)))
        self.assertEqual(total, sum(item.size for item in result['items']))


if __name__ == '__main__':
    unittest.main()
