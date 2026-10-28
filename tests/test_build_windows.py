"""Distribution integrity and rejection of invalid signing responses."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import build_windows


class WindowsDistributionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        preferences = self.root/'cplus_cleaner/application/preferences.py'
        preferences.parent.mkdir(parents=True)
        preferences.write_text('VERSION = "1.2.3"\nraise RuntimeError("Do not import preferences")\n',
                               encoding='utf-8')
        (self.root/'LICENSE').write_text('Test license\n', encoding='utf-8')
        self.executable = self.root/'signed/CPlusCleaner.exe'
        self.executable.parent.mkdir()
        data = bytearray(256)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 0x3c, 128)
        data[128:132] = b'PE\0\0'
        struct.pack_into('<H', data, 132, 0x8664)
        struct.pack_into('<H', data, 128+24+68, 2)
        self.executable.write_bytes(data+b'opaque-test-signature')
        self.signature = dict(status='Valid', signature_type='Authenticode',
                              publisher='SignPath Foundation', timestamped=True,
                              product_name='CPlus Cleaner', product_version='1.2.3',
                              file_version='1.2.3', original_filename='CPlusCleaner.exe')

    def query(self, signature):
        return patch.object(build_windows.subprocess, 'run', return_value=subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(signature), stderr=''))

    def test_version_is_read_without_importing_application(self):
        self.assertEqual(build_windows.application_version(self.root), '1.2.3')

    def test_packaging_uses_signed_bytes_for_zip_and_both_checksums(self):
        # Simulate stale pre-signing artifacts: none may survive re-packaging.
        (self.root/'dist').mkdir()
        (self.root/'dist/CPlusCleaner.exe').write_bytes(b'old unsigned executable')
        (self.root/'dist/SHA256SUMS.txt').write_text('old checksums', encoding='utf-8')
        with self.query(self.signature), patch.object(build_windows, 'smoke_test',
                                                     return_value={'ok': True, 'frozen': True}):
            build_windows.package_distribution(self.executable, root=self.root, require_signed=True)
        distribution = self.root/'dist'
        expected = self.executable.read_bytes()
        self.assertEqual((distribution/'CPlusCleaner.exe').read_bytes(), expected)
        with zipfile.ZipFile(distribution/'CPlusCleaner-windows-x64.zip') as bundle:
            self.assertEqual(set(bundle.namelist()), {'CPlusCleaner.exe', 'LICENSE', 'README.txt'})
            self.assertEqual(bundle.read('CPlusCleaner.exe'), expected)
        for line in (distribution/'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines():
            digest, name = line.split('  ')
            self.assertEqual(digest, hashlib.sha256((distribution/name).read_bytes()).hexdigest())

    def test_invalid_signature_is_rejected_before_running_or_publishing(self):
        for key, invalid in [('status', 'NotSigned'), ('status', 'NotTrusted'),
                             ('status', 'HashMismatch'), ('signature_type', 'Catalog'),
                             ('publisher', 'Someone else'), ('timestamped', False),
                             ('product_name', 'Another app'), ('product_version', '0.0.1'),
                             ('file_version', '0.0.1'), ('original_filename', 'AnotherApp.exe')]:
            with self.subTest(key=key, invalid=invalid):
                signature = dict(self.signature, **{key: invalid})
                with self.query(signature), patch.object(build_windows, 'smoke_test') as launch:
                    with self.assertRaises(ValueError):
                        build_windows.package_distribution(self.executable, root=self.root,
                                                           require_signed=True)
                    launch.assert_not_called()
                self.assertFalse((self.root/'dist').exists())

    def test_pe_architecture_and_subsystem_are_verified(self):
        data = bytearray(self.executable.read_bytes())
        for field, invalid in [(132, 0x014c), (128+24+68, 3)]:
            with self.subTest(field=field):
                modified = bytearray(data)
                struct.pack_into('<H', modified, field, invalid)
                self.executable.write_bytes(modified)
                with self.assertRaises(ValueError):
                    build_windows.validate_executable(self.executable)
        self.executable.write_bytes(b'MZ')
        with self.assertRaises(ValueError):
            build_windows.validate_executable(self.executable)

    def test_failed_windows_verification_does_not_launch_or_publish(self):
        failed = subprocess.CompletedProcess([], 1, stdout='', stderr='verification unavailable')
        with patch.object(build_windows.subprocess, 'run', return_value=failed), \
                patch.object(build_windows, 'smoke_test') as launch:
            with self.assertRaisesRegex(RuntimeError, 'verification unavailable'):
                build_windows.package_distribution(self.executable, root=self.root,
                                                   require_signed=True)
            launch.assert_not_called()
        self.assertFalse((self.root/'dist').exists())

    def test_signature_path_is_data_and_does_not_change_certificate_trust(self):
        with self.query(self.signature) as run:
            build_windows.verify_signature(self.executable,
                                           expected_publisher='SignPath Foundation', root=self.root)
        arguments = run.call_args
        self.assertEqual(arguments.kwargs['env']['CPLUS_SIGNATURE_FILE'], str(self.executable))
        self.assertNotIn(str(self.executable), arguments.args[0][-1])
        self.assertIn('Get-AuthenticodeSignature', arguments.args[0][-1])


if __name__ == '__main__':
    unittest.main()

# Purpose: Signed distribution integrity and failure handling regression tests
