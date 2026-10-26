from dataclasses import replace
from pathlib import Path
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.features.uninstall import software_uninstall as software
from cplus_cleaner.features.uninstall import uninstall_residue as residue
from cplus_cleaner.features.uninstall import uninstall_windows


class Key:
    def __init__(self, hive, path, view):
        self.hive, self.path, self.view = hive, path, view
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class Registry:
    HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER = 'HKLM', 'HKCU'
    KEY_READ, KEY_WOW64_32KEY, KEY_WOW64_64KEY = 1, 0x200, 0x100
    def __init__(self):
        self.data, self.changed = {}, 1
    def put(self, hive, path, values, view=64):
        self.data[hive,path,view] = dict(values)
        parent = path
        while '\\' in parent:
            parent = parent.rsplit('\\',1)[0]
            self.data.setdefault((hive,parent,view), {})
    def children(self, key):
        prefix = key.path+'\\'
        return sorted(p[len(prefix):] for h,p,v in self.data
            if h == key.hive and v == key.view and p.startswith(prefix) and '\\' not in p[len(prefix):])
    def OpenKey(self, hive, path, reserved=0, access=1):
        if isinstance(hive, Key):
            view = hive.view
            path, hive = hive.path+'\\'+path, hive.hive
        else:
            view = 32 if access & self.KEY_WOW64_32KEY else 64
        if (hive,path,view) not in self.data:
            raise FileNotFoundError(path)
        return Key(hive,path,view)
    def QueryInfoKey(self, key):
        return len(self.children(key)), len(self.data[key.hive,key.path,key.view]), self.changed
    def EnumKey(self, key, index):
        return self.children(key)[index]
    def QueryValueEx(self, key, name):
        values = self.data[key.hive,key.path,key.view]
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1
    def EnumValue(self, key, index):
        name, value = list(self.data[key.hive,key.path,key.view].items())[index]
        return name,value,1
    def DeleteKeyEx(self, hive, path, access=0, reserved=0):
        view = 32 if access & self.KEY_WOW64_32KEY else 64
        if self.children(Key(hive,path,view)):
            raise OSError('has subkeys')
        del self.data[hive,path,view]


class FakeProcess:
    def __init__(self, callback=lambda: None, code=0):
        self.callback, self.code, self.closed = callback, code, False
    def wait(self, **kwargs):
        self.callback()
        return self.code
    def close(self):
        self.closed = True


class SoftwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uninstall-fixture-', dir=core.APP_DIR.parent)
        self.base = Path(self.temp.name)
        self.folder = self.base/'Demo App'
        self.folder.mkdir()
        self.exe = self.folder/'uninstall.exe'
        self.exe.write_bytes(b'x'*1025)
        (self.folder/'data.bin').write_bytes(b'x'*3030)
        self.registry = Registry()
        self.record = dict(DisplayName='Demo App',DisplayIcon='',UninstallString=f'"{self.exe}" --id "value with spaces"',
            QuietUninstallString='must-not-be-used', InstallLocation=str(self.folder), InstallDate='20261023',
            EstimatedSize=9999999, _source=dict(hive='HKCU',view=64,subkey='demo'))
        self.registry.put('HKCU',software.UNINSTALL+'\\demo',{k:v for k,v in self.record.items() if k in software.FIELDS})
        self.patches = [patch.object(software,'winreg',self.registry),patch.object(core,'DATA_DIR',self.base/'audit')]
        for p in self.patches:
            p.start()
        with software._LOCK:
            software._ISSUED.clear()
            software._COMPLETED.clear()
    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()
    def scan(self, records=None):
        return software.scan(records=[self.record] if records is None else records, icons=False)

    def test_native_filters_sizes_dates_and_multiple_quoted_arguments(self):
        result = self.scan()
        self.assertEqual(len(result['items']),1)
        item = result['items'][0]
        self.assertEqual(item.size, 3*1024)
        self.assertEqual(item.install_time,'20261023')
        self.assertEqual(software.native_path(item.command),self.exe)
        line, args = uninstall_windows.command_parts(item.command,self.exe)
        self.assertEqual(args,'--id "value with spaces"')
        self.assertIn(str(self.exe),line)

    def test_invalid_icon_uninstaller_component_and_original_self_are_excluded(self):
        invalid = [dict(self.record,SystemComponent=1),dict(self.record,DisplayName=''),
            dict(self.record,DisplayIcon=str(self.base/'missing.exe')),
            dict(self.record,UninstallString=str(self.base/'missing.exe'))]
        own = self.base/'FanQieClean'
        own.mkdir()
        (own/'uninstall.exe').write_bytes(b'x')
        invalid.append(dict(self.record,UninstallString=str(own/'uninstall.exe')))
        self.assertEqual(self.scan(invalid)['items'],[])
        # Native does not use NoRemove or ParentKeyName as exclusion rules.
        self.assertEqual(len(self.scan([dict(self.record,NoRemove=1,ParentKeyName='parent')])['items']),1)

    def test_location_fallbacks_and_creation_date(self):
        item = self.scan([dict(self.record,InstallLocation='missing',LocationRoot=str(self.folder),InstallDate='')])['items'][0]
        self.assertEqual(item.location,str(self.folder))
        self.assertRegex(item.install_time,r'^\d{4}/\d{2}/\d{2} \d{2}:\d{2}$')
        item = self.scan([dict(self.record,InstallLocation='missing')])['items'][0]
        self.assertEqual(item.location,str(self.folder))

    def test_msi_relative_executable_parser_and_unquoted_command_validation(self):
        (self.base/'msiexec.exe').write_bytes(b'x')
        self.assertEqual(software.native_path('MsiExec.exe /I{fake}',system=self.base),self.base/'msiexec.exe')
        self.assertFalse(software.valid_executable(software.native_path(str(self.exe)+' /quiet')))

    def test_relative_msi_without_install_location_does_not_count_system_directory(self):
        (self.base/'msiexec.exe').write_bytes(b'exe')
        record = dict(self.record,UninstallString='MsiExec.exe /I{fake}',InstallLocation='',LocationRoot='')
        with patch.object(software,'system_directory',return_value=self.base):
            self.assertFalse(self.scan([record])['items'])
            self.assertEqual(len(self.scan([dict(record,InstallLocation=str(self.folder))])['items']),1)

    def test_registry_hive_key_dedup_prefers_later_64_view(self):
        self.registry.put('HKCU',software.UNINSTALL+'\\demo',dict(DisplayName='32'),view=32)
        self.registry.put('HKLM',software.UNINSTALL+'\\demo',dict(DisplayName='machine'),view=64)
        records, errors = software.registry_records()
        self.assertFalse(errors)
        self.assertEqual(len(records),2)
        user = next(r for r in records if r['_source']['hive']=='HKCU')
        self.assertEqual(user['_source']['view'],64)
        self.assertEqual(user['DisplayName'],'Demo App')

    def test_invalid_common_record_does_not_shadow_valid_32_view(self):
        values = {k:v for k,v in self.record.items() if k in software.FIELDS}
        self.registry.put('HKCU',software.UNINSTALL+'\\demo',values,view=32)
        self.registry.put('HKCU',software.UNINSTALL+'\\demo',dict(values,SystemComponent=1),view=64)
        records, errors = software.registry_records()
        self.assertFalse(errors)
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['_source']['view'],32)

    def test_registered_command_used_and_registry_removal_confirms_success(self):
        item = self.scan()['items'][0]
        process = FakeProcess(lambda:self.registry.DeleteKeyEx('HKCU',software.UNINSTALL+'\\demo',0x100))
        with patch.object(uninstall_windows,'launch',return_value=process) as launch:
            result = software.uninstall(item)
        self.assertEqual(result['status'],'success')
        launch.assert_called_once_with(item.command,self.exe)
        self.assertTrue(process.closed)
        self.assertTrue(self.exe.exists())

    def test_zero_exit_is_pending_and_nonzero_is_failure_when_still_installed(self):
        item = self.scan()['items'][0]
        self.assertEqual(software.uninstall(item,launcher=lambda *a:FakeProcess())['status'],'pending')
        self.assertEqual(software.uninstall(item,launcher=lambda *a:FakeProcess(code=5))['status'],'failed')
        self.assertEqual(software.uninstall(item,launcher=lambda *a:FakeProcess(code=1602))['status'],'cancelled')

    def test_disappeared_uninstaller_confirms_original_success_condition(self):
        item = self.scan()['items'][0]
        result = software.uninstall(item,launcher=lambda *a:FakeProcess(self.exe.unlink))
        self.assertEqual(result['status'],'success')

    def test_changed_or_forged_inventory_cannot_launch(self):
        item = self.scan()['items'][0]
        launch = unittest.mock.Mock()
        self.registry.data['HKCU',software.UNINSTALL+'\\demo',64]['UninstallString'] = 'changed'
        self.assertEqual(software.uninstall(item,launcher=launch)['status'],'failed')
        self.assertEqual(software.uninstall(replace(item,command='forged'),launcher=launch)['status'],'failed')
        launch.assert_not_called()

    def test_cancel_before_start_does_not_launch(self):
        item = self.scan()['items'][0]
        cancel = threading.Event();cancel.set()
        launch = unittest.mock.Mock()
        self.assertEqual(software.uninstall(item,cancel,launcher=launch)['status'],'cancelled')
        launch.assert_not_called()

    def test_residue_manifest_is_scoped_rechecks_changes_and_keeps_new_files(self):
        bases = {key:self.base/key for key in ('local','roaming','data','programs','programs_x86','common_start','user_start')}
        for path in bases.values():path.mkdir()
        app = bases['programs']/'Demo'
        app.mkdir()
        executable = app/'uninstall.exe';executable.write_bytes(b'exe')
        old = app/'old.bin';old.write_bytes(b'old data')
        changed = app/'changed.bin';changed.write_bytes(b'original')
        empty = app/'empty';empty.touch()
        record = dict(self.record,DisplayName='Demo',InstallLocation=str(app),UninstallString=f'"{executable}"')
        self.registry.put('HKCU',software.UNINSTALL+'\\demo',{k:v for k,v in record.items() if k in software.FIELDS})
        item = self.scan([record])['items'][0]
        software.uninstall(item,launcher=lambda *a:FakeProcess(lambda:self.registry.DeleteKeyEx('HKCU',software.UNINSTALL+'\\demo',0x100)))
        self.registry.put('HKCU',r'SOFTWARE\Demo',{'value':'remove'})
        menu = bases['user_start']/'Programs';menu.mkdir()
        shortcut = menu/'Demo.lnk';shortcut.touch()
        manifest = residue.scan_residue(item,bases=bases,registry=self.registry)
        self.assertIn(str(old),[f['path'] for f in manifest['files']])
        self.assertIn(str(shortcut),[f['path'] for f in manifest['files']])
        self.assertNotIn(str(empty),[f['path'] for f in manifest['files']])
        changed.write_bytes(b'changed since scan')
        new = app/'new.bin';new.write_bytes(b'keep new')
        result = residue.clean_residue(manifest,registry=self.registry)
        self.assertGreater(result['deleted'],0)
        self.assertTrue(result['errors'])
        self.assertTrue(changed.exists());self.assertTrue(new.exists());self.assertTrue(empty.exists())
        self.assertFalse(old.exists());self.assertFalse(shortcut.exists())
        self.assertTrue(app.is_dir())
        self.assertNotIn(('HKCU',r'SOFTWARE\Demo',64),self.registry.data)

    def test_residue_requires_confirmed_uninstall(self):
        item = self.scan()['items'][0]
        with self.assertRaises(ValueError):residue.scan_residue(item)

    def residue_fixture(self, others=None):
        bases = {key:self.base/key for key in ('local','roaming','data','programs','programs_x86','common_start','user_start')}
        for path in bases.values():path.mkdir()
        app = bases['programs']/'Demo';app.mkdir()
        exe = app/'uninstall.exe';exe.write_bytes(b'exe')
        sample = app/'sample.bin';sample.write_bytes(b'keep unless confirmed')
        record = dict(self.record,DisplayName='Demo',InstallLocation=str(app),UninstallString=f'"{exe}"')
        self.registry.put('HKCU',software.UNINSTALL+'\\demo',{k:v for k,v in record.items() if k in software.FIELDS})
        item = self.scan([record])['items'][0]
        software.uninstall(item,launcher=lambda *a:FakeProcess(lambda:self.registry.DeleteKeyEx('HKCU',software.UNINSTALL+'\\demo',0x100)))
        self.registry.put('HKCU',r'SOFTWARE\Demo',{'value':'original'})
        manifest = residue.scan_residue(item,bases=bases,registry=self.registry,others=others)
        return item, sample, manifest

    def test_forged_residue_entry_cannot_delete_unscanned_file(self):
        item, sample, manifest = self.residue_fixture()
        outside = self.base/'unrelated.bin';outside.write_bytes(b'preserve')
        forged = dict(manifest,files=[dict(manifest['files'][0],path=str(outside))],registry=[])
        result = residue.clean_residue(forged,registry=self.registry)
        self.assertEqual(result['deleted'],0)
        self.assertTrue(result['errors']);self.assertTrue(outside.exists());self.assertTrue(sample.exists())

    def test_residue_reinstall_or_registry_changes_preserve_data(self):
        item, sample, manifest = self.residue_fixture()
        self.registry.data['HKCU',r'SOFTWARE\Demo',64]['value'] = 'new settings'
        result = residue.clean_residue(dict(manifest,files=[]),registry=self.registry)
        self.assertEqual(result['deleted'],0);self.assertTrue(result['errors'])
        self.assertIn(('HKCU',r'SOFTWARE\Demo',64),self.registry.data)
        self.registry.put('HKCU',software.UNINSTALL+'\\demo',dict(item.source['snapshot'],DisplayVersion='reinstalled'))
        result = residue.clean_residue(manifest,registry=self.registry)
        self.assertEqual(result['deleted'],0);self.assertTrue(result['errors']);self.assertTrue(sample.exists())

    def test_residue_cancellation_does_not_delete_and_shared_directory_is_excluded(self):
        item, sample, manifest = self.residue_fixture()
        cancel = threading.Event();cancel.set()
        result = residue.clean_residue(manifest,cancel,registry=self.registry)
        self.assertEqual(result['deleted'],0);self.assertTrue(result['cancelled']);self.assertTrue(sample.exists())
        other = replace(item,key='another',name='Another',location=str(sample.parent/'child'))
        bases = {key:self.base/key for key in ('local','roaming','data','programs','programs_x86','common_start','user_start')}
        shared = residue.scan_residue(item,bases=bases,registry=self.registry,others=[other])
        self.assertFalse(shared['files']);self.assertTrue(shared['errors'])

    @unittest.skipUnless(os.name == 'nt','Windows job monitoring')
    def test_windows_job_waits_for_a_benign_python_child_after_parent_exits(self):
        pythonw = Path(sys.executable).with_name('pythonw.exe')
        if not pythonw.exists():self.skipTest('No windowless Python for fixture helper')
        done = self.base/'child-finished.txt'
        child = "import time,pathlib;time.sleep(.3);pathlib.Path("+repr(str(done))+").write_text('done')"
        helper = self.base/'job-helper.py'
        helper.write_text('import subprocess,sys\nsubprocess.Popen([sys.executable,"-c",'+repr(child)+'])\n','utf-8')
        process = uninstall_windows.launch(f'"{pythonw}" "{helper}"',pythonw)
        try:
            start = time.monotonic()
            self.assertEqual(process.wait(),0)
            self.assertGreater(time.monotonic()-start,.15)
            self.assertTrue(done.exists(),'The whole process job, including the child, must finish')
        finally:process.close()


if __name__=='__main__':unittest.main()

# Purpose: Regression tests for uninstall candidates, batch flow and results
