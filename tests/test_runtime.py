"""Source/frozen launch commands and portable self-protection use isolated files."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core, runtime
from cplus_cleaner.application import preferences
from cplus_cleaner.features.drive_cleanup import cdrive
from cplus_cleaner.features.large_files import large_files
from cplus_cleaner.features.uninstall import uninstall_residue


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=core.APP_DIR.parent)
        self.base = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_source_command_prefers_pythonw_and_keeps_entry_script(self):
        python = self.base/'Python runtime/python.exe'
        python.parent.mkdir()
        python.write_bytes(b'fixture')
        windowed = python.with_name('pythonw.exe')
        with patch.object(runtime.sys, 'frozen', False, create=True), patch.object(runtime.sys, 'executable', str(python)):
            expected = [str(python), str(runtime.resource_root()/'app.py'), '--background']
            self.assertEqual(runtime.launch_command('--background'), expected)
            windowed.write_bytes(b'fixture')
            expected[0] = str(windowed)
            self.assertEqual(runtime.launch_command('--background'), expected)

    def test_frozen_commands_use_exe_and_extracted_resources(self):
        exe = self.base/'Portable folder/CPlusCleaner.exe'
        payload = self.base/'extracted'
        with patch.object(runtime.sys, 'frozen', True, create=True), \
                patch.object(runtime.sys, '_MEIPASS', str(payload), create=True), \
                patch.object(runtime.sys, 'executable', str(exe)):
            self.assertEqual(runtime.resource_root(), payload.resolve())
            self.assertEqual(runtime.application_root(), exe.parent.resolve())
            self.assertEqual(runtime.launch_command('--background'), [str(exe), '--background'])
            self.assertEqual(runtime.launch_command('--component-analysis-job', 'job'),
                             [str(exe), '--component-analysis-job', 'job'])
            self.assertIn('--background', preferences.startup_command())
            self.assertNotIn('app.py', preferences.startup_command())
            self.assertNotIn('pythonw.exe', preferences.startup_command())

    def test_portable_exe_is_protected_without_excluding_other_downloads(self):
        payload, downloads = self.base/'extracted', self.base/'Downloads'
        payload.mkdir(); downloads.mkdir()
        exe, other = downloads/'CPlusCleaner.exe', downloads/'OtherInstaller.exe'
        exe.write_bytes(b'executable fixture'); other.write_bytes(b'other fixture')
        with patch.object(runtime.sys, 'frozen', True, create=True), \
                patch.object(runtime.sys, 'executable', str(exe)), patch.object(core, 'APP_DIR', payload):
            self.assertIn(exe, core.application_roots())
            self.assertNotIn(downloads, large_files.exclusions())
            self.assertEqual(core.check_path(other), other)
            with self.assertRaises(ValueError):
                core.check_path(exe)
            with patch.object(cdrive, 'authorize', return_value=True), self.assertRaises(ValueError):
                core.check_path(exe, scope_id='fixture-authorized')
            self.assertEqual(uninstall_residue._path_guard(other, []), '')
            self.assertEqual(uninstall_residue._path_guard(exe, []), '属于当前运行程序')


if __name__ == '__main__':
    unittest.main()

# Purpose: Frozen resource, self-launch and portable executable protection tests
