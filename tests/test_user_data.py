"""Profile migration checks use workspace-only data and never touch the real profile."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cplus_cleaner.common import core, user_data
from cplus_cleaner.application import preferences, single_instance
from cplus_cleaner.features.app_compression import software_compression
from cplus_cleaner.features.app_mover import software_migration


class UserDataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='user-data-', dir=core.APP_DIR.parent)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.old = self.base/'CCLEANNER'
        self.new = self.base/'CPLUSCLEANER'

    def fixture(self):
        values = {
            'settings.json': json.dumps(dict(preferences.DEFAULTS, language='en')),
            'software-migrations.jsonl': json.dumps(dict(id='move', status='fixture', source='C:/Apps/Demo'))+'\n',
            'operations.jsonl': '{"action":"fixture"}\n',
            'popup/state.json': '{"enabled":true,"rules":{"fixture":false}}',
            'popup/results.json': '{"version":1,"groups":[]}',
            'compression/records.json': json.dumps([dict(id='a'*32, processed=1, restored=False)]),
            'compression/'+('a'*32)+'.json': '{"files":[],"folder":"C:/Apps/Demo"}',
        }
        for relative, text in values.items():
            path = self.old/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        return {name: hashlib.sha256((self.old/name).read_bytes()).hexdigest() for name in values}

    def test_directory_rename_preserves_settings_records_manifests_and_file_identities(self):
        hashes = self.fixture()
        info = (self.old/'settings.json').stat()
        self.assertEqual(user_data.migrate_legacy_data(self.base), self.new)
        self.assertFalse(self.old.exists())
        self.assertEqual(hashes, {name: hashlib.sha256((self.new/name).read_bytes()).hexdigest() for name in hashes})
        after = (self.new/'settings.json').stat()
        self.assertEqual((info.st_ino, info.st_size, info.st_mtime_ns),
                         (after.st_ino, after.st_size, after.st_mtime_ns))
        with patch.object(core, 'DATA_DIR', self.new):
            self.assertEqual(preferences.load()['language'], 'en')
            self.assertEqual(software_migration.records()[0]['id'], 'move')
            self.assertTrue(software_compression.history()[0]['can_restore'])
        self.assertEqual(user_data.migrate_legacy_data(self.base), self.new)
        self.assertEqual(hashes, {name: hashlib.sha256((self.new/name).read_bytes()).hexdigest() for name in hashes})

    def test_nonempty_new_profile_has_priority_without_overwriting_either_directory(self):
        self.fixture()
        self.new.mkdir()
        (self.new/'settings.json').write_text('new settings', encoding='utf-8')
        self.assertEqual(user_data.migrate_legacy_data(self.base), self.new)
        self.assertEqual((self.new/'settings.json').read_text(), 'new settings')
        self.assertTrue((self.old/'compression/records.json').is_file())
        self.assertTrue((self.old/'settings.json').is_file())

    def test_empty_destination_is_migrated_and_absent_legacy_has_no_side_effects(self):
        self.assertEqual(user_data.migrate_legacy_data(self.base), self.new)
        self.assertFalse(self.new.exists())
        self.fixture()
        self.new.mkdir()
        user_data.migrate_legacy_data(self.base)
        self.assertTrue((self.new/'settings.json').is_file())

    def test_failed_rename_preserves_old_files_and_empty_destination(self):
        hashes = self.fixture()
        self.new.mkdir()
        with patch.object(Path, 'rename', side_effect=PermissionError('fixture denied')):
            with self.assertRaises(PermissionError):
                user_data.migrate_legacy_data(self.base)
        self.assertTrue(self.new.is_dir())
        self.assertEqual(list(self.new.iterdir()), [])
        self.assertEqual(hashes, {name: hashlib.sha256((self.old/name).read_bytes()).hexdigest() for name in hashes})

    def test_redirected_or_file_destination_is_rejected_without_moving_legacy(self):
        self.fixture()
        self.new.write_text('unrelated file', encoding='utf-8')
        with self.assertRaises(OSError):
            user_data.migrate_legacy_data(self.base)
        self.assertEqual(self.new.read_text(), 'unrelated file')
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)), \
                patch.object(Path, 'rename') as move:
            with self.assertRaises(OSError):
                user_data.migrate_legacy_data(self.base)
            move.assert_not_called()

    def test_import_and_default_path_lookup_do_not_migrate_or_create_data(self):
        self.fixture()
        env = dict(os.environ, LOCALAPPDATA=str(self.base))
        result = subprocess.run([sys.executable, '-c',
            'from cplus_cleaner.common import core; print(core.DATA_DIR.name)'],
            cwd=core.APP_DIR, env=env, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'CPLUSCLEANER')
        self.assertTrue(self.old.is_dir())
        self.assertFalse(self.new.exists())


class UserDataLaunchTests(unittest.TestCase):
    def test_primary_launch_migrates_before_desktop_loads_settings_and_duplicates_do_not(self):
        for primary in (True, False):
            order = []
            instance = Mock(primary=primary)
            guard = Mock()
            guard.__enter__ = Mock(return_value=instance)
            guard.__exit__ = Mock(return_value=False)
            desktop = Mock()
            desktop.mainloop.side_effect = lambda: order.append('mainloop')
            with patch.object(single_instance, 'SingleInstance', return_value=guard), \
                    patch.object(user_data, 'migrate_legacy_data', side_effect=lambda: order.append('migrate')) as migrate, \
                    patch('cplus_cleaner.ui.desktop.DesktopApplication', side_effect=lambda **kwargs:
                          (order.append('desktop'), desktop)[1]) as create:
                single_instance.run()
                self.assertEqual(order, ['migrate', 'desktop', 'mainloop'] if primary else [])
                if not primary:
                    migrate.assert_not_called()
                    create.assert_not_called()

    def test_failed_migration_does_not_open_a_blank_desktop_or_write_default_settings(self):
        guard = Mock()
        guard.__enter__ = Mock(return_value=Mock(primary=True))
        guard.__exit__ = Mock(return_value=False)
        with patch.object(single_instance, 'SingleInstance', return_value=guard), \
                patch.object(user_data, 'migrate_legacy_data', side_effect=PermissionError('fixture denied')), \
                patch('cplus_cleaner.ui.desktop.DesktopApplication') as create, \
                patch('cplus_cleaner.localization.localized_tk.messagebox.showerror') as error:
            single_instance.run()
            create.assert_not_called()
            error.assert_called_once()


# Purpose: Regression tests for user-data migration, recovery preservation and guarded startup
