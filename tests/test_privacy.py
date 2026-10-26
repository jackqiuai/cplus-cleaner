from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.features.privacy import privacy
from cplus_cleaner.features.privacy import privacy_windows as windows


class Key:
    def __init__(self, path):
        self.path = path
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class Registry:
    HKEY_CURRENT_USER = 'hkcu'
    KEY_READ = 1
    KEY_SET_VALUE = 2

    def __init__(self, entries):
        self.entries = entries
    def OpenKey(self, hive, path, reserved=0, access=1):
        if hive != self.HKEY_CURRENT_USER or path not in self.entries:
            raise FileNotFoundError(path)
        return Key(path)
    def children(self, path):
        return [key[len(path)+1:] for key in self.entries
                if key.startswith(path+'\\') and '\\' not in key[len(path)+1:]]
    def QueryInfoKey(self, key):
        return len(self.children(key.path)), len(self.entries[key.path]), 0
    def EnumValue(self, key, index):
        name, (value, typ) = list(self.entries[key.path].items())[index]
        return name, value, typ
    def EnumKey(self, key, index):
        return self.children(key.path)[index]
    def QueryValueEx(self, key, name):
        if name not in self.entries[key.path]:
            raise FileNotFoundError(name)
        return self.entries[key.path][name]
    def DeleteValue(self, key, name):
        del self.entries[key.path][name]
    def SetValueEx(self, key, name, reserved, typ, value):
        self.entries[key.path][name] = value, typ
    def DeleteKey(self, hive, path):
        if self.children(path) or self.entries[path]:
            raise OSError('not empty')
        del self.entries[path]


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='privacy-fixture-', dir=core.APP_DIR.parent)
        self.base = Path(self.temp.name)
        self.registry = Registry({
            windows.PATHS['run']: {'a': ('one', 1), 'b': ('two', 1), 'MRUList': ('ba', 1)},
            windows.PATHS['app_switched']: {'app1': (2, 4), 'app2': (3, 4)},
            windows.PATHS['regedit']: {'LastKey': ('private path', 1), 'View': (1, 4)},
            windows.PATHS['recent_docs']: {},
            windows.PATHS['recent_docs']+'\\.pdf': {'0': (b'private', 3), 'MRUListEx': (b'\0\0\0\0\xff\xff\xff\xff', 3)},
            windows.EXPLORER+'\\FileExts\\.pdf\\UserChoice': {'ProgId': ('keep association', 1)},
        })
    def tearDown(self):
        self.temp.cleanup()
    def scan(self, **kwargs):
        return privacy.scan(registry=self.registry, thunder_paths=[], browser_roots={}, **kwargs)

    def test_four_groups_and_thirteen_original_categories_count_records(self):
        session = self.scan()
        self.assertEqual(len(session.categories), 13)
        self.assertEqual([g[1] for g in privacy.GROUPS], ['Windows运行痕迹','软件使用痕迹','浏览器记录','高危隐私'])
        self.assertEqual(session.total_count, 7)
        self.assertEqual(session.selected_count, 7)
        self.assertEqual(session.categories['app_switched'].count, 2)
        self.assertEqual(session.categories['extensions'].count, 1)
        self.assertEqual(session.categories['start_recent'].count, 0)

    def test_selection_and_group_selection(self):
        session = self.scan()
        session.set_group('windows', False)
        self.assertEqual(session.selected_count, 0)
        session.toggle('app_switched')
        self.assertEqual(session.selected_count, 2)
        session.toggle('start_recent')
        self.assertTrue(session.categories['start_recent'].view_only)

    def test_registry_cleanup_preserves_associations_and_new_values(self):
        records = windows.scan_registry(registry=self.registry)['items']
        run = [i for i in records if i['category'] == 'run']
        self.registry.entries[windows.PATHS['run']]['c'] = ('new', 1)
        self.registry.entries[windows.PATHS['run']]['MRUList'] = ('cba', 1)
        result = windows.clean_registry(run, registry=self.registry)
        self.assertEqual(len(result['cleaned_ids']), 2)
        self.assertFalse(result['errors'])
        self.assertEqual(self.registry.entries[windows.PATHS['run']], {'c': ('new', 1), 'MRUList': ('c', 1)})
        self.assertEqual(self.registry.entries[windows.EXPLORER+'\\FileExts\\.pdf\\UserChoice']['ProgId'][0], 'keep association')

    def test_changed_registry_value_is_retained(self):
        item = next(i for i in windows.scan_registry(registry=self.registry)['items'] if i['category']=='regedit')
        self.registry.entries[item['source']]['LastKey'] = ('changed', 1)
        result = windows.clean_registry([item], registry=self.registry)
        self.assertEqual(result['cleaned_ids'], [])
        self.assertEqual(len(result['errors']), 1)

    def test_forged_registry_scope_rejected(self):
        item = next(i for i in windows.scan_registry(registry=self.registry)['items'] if i['category']=='regedit')
        result = windows.clean_registry([dict(item, source='Software\\Unrelated')], registry=self.registry)
        self.assertTrue(result['errors'])
        self.assertIn('LastKey', self.registry.entries[item['source']])

    def test_changed_extension_group_retained(self):
        item = next(i for i in windows.scan_registry(registry=self.registry)['items'] if i['category']=='extensions')
        self.registry.entries[item['source']]['1'] = (b'new', 3)
        result = windows.clean_registry([item], registry=self.registry)
        self.assertFalse(result['cleaned_ids'])
        self.assertTrue(result['errors'])
        self.assertIn('0', self.registry.entries[item['source']])

    def test_overlapping_registry_categories_clean_once(self):
        session = self.scan()
        with patch.object(windows, 'winreg', self.registry):
            result = privacy.clean(session)
        self.assertFalse(result['errors'])
        self.assertEqual(session.total_count, 0)
        self.assertEqual(result['cleaned_count'], 7)
        self.assertNotIn(windows.PATHS['recent_docs']+'\\.pdf', self.registry.entries)
        self.assertEqual(self.registry.entries[windows.PATHS['regedit']], {'View': (1, 4)})

    def test_cancel_does_not_write_registry(self):
        session = self.scan()
        cancel = threading.Event()
        cancel.set()
        with patch.object(windows, 'winreg', self.registry):
            result = privacy.clean(session, cancel)
        self.assertTrue(result['cancelled'])
        self.assertEqual(session.total_count, 7)

    def database(self):
        path = self.base/'TaskDb.dat'
        with closing(sqlite3.connect(path)) as db:
            db.executescript('CREATE TABLE BtFile(FileName TEXT); CREATE TABLE TaskBase(Name TEXT);'
                             "INSERT INTO BtFile VALUES('old.zip'); INSERT INTO TaskBase VALUES('keep task');")
            db.commit()
        return path

    def test_thunder_row_count_cleanup_preserves_new_rows_and_files(self):
        path = self.database()
        download = self.base/'old.zip'
        download.write_bytes(b'keep downloaded content')
        result = windows.scan_thunder(paths=[path])
        self.assertEqual(len(result['items']), 1)
        with closing(sqlite3.connect(path)) as db:
            db.execute("INSERT INTO BtFile VALUES('new.zip')")
            db.commit()
        with patch.object(windows.software_windows, 'running_images', return_value=[]):
            cleaned = windows.clean_thunder(result['items'])
        self.assertFalse(cleaned['errors'])
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('SELECT FileName FROM BtFile').fetchall(), [('new.zip',)])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM TaskBase').fetchone()[0], 1)
        self.assertTrue(download.exists())

    def test_running_thunder_refuses_cleanup(self):
        path = self.database()
        result = windows.scan_thunder(paths=[path])
        with patch.object(windows.software_windows, 'running_images', return_value=['C:/Thunder.exe']):
            cleaned = windows.clean_thunder(result['items'])
        self.assertFalse(cleaned['cleaned_ids'])
        self.assertTrue(cleaned['errors'])

    def test_cancel_during_thunder_snapshot_returns_cancelled_result(self):
        path = self.database()
        cancel = threading.Event()
        def interrupt(*args):
            cancel.set()
            raise windows.privacy_browser.Cancelled()
        with patch.object(windows.privacy_browser, '_snapshot', side_effect=interrupt):
            result = windows.scan_thunder(cancel, paths=[path])
        self.assertTrue(result['cancelled'])
        self.assertFalse(result['items'])
        self.assertFalse(result['diagnostics'][0].get('status') == 'error')

    def test_overlapping_browser_categories_reconcile_members_once(self):
        session = privacy.Session()
        session.scanned = True
        one = dict(id='one', kind='browser')
        two = dict(id='two', kind='browser')
        session.categories['chrome_history'].records = [one]
        session.categories['edge_history'].records = [two]
        session.categories['shopping'].records = [dict(id='shop', kind='browser_group', members=[one,two])]
        with patch.object(privacy.privacy_browser, 'clean', return_value=dict(cleaned_ids=['one'],errors=[])):
            privacy.clean(session)
        self.assertEqual(session.total_count, 2)
        self.assertEqual(session.categories['shopping'].records[0]['members'], [two])

    def test_unreadable_source_is_reported(self):
        with patch.object(windows, '_values', side_effect=PermissionError('denied')):
            session = self.scan()
        self.assertTrue(session.problems)
        self.assertTrue(session.categories['run'].problems)


if __name__ == '__main__':
    unittest.main()
