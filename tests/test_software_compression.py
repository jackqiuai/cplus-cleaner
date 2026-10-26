from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import core
import software_compression as compression
import software_uninstall as inventory
from test_software_uninstall import Registry


class CompressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='compression-fixture-',dir=core.APP_DIR.parent)
        self.base = Path(self.temp.name)
        self.root = self.base/'Demo';self.root.mkdir()
        self.exe = self.root/'uninstall.exe';self.exe.write_bytes(b'exe'*1024)
        self.file = self.root/'data.bin';self.file.write_bytes(b'compressible data\n'*10000)
        self.registry = Registry()
        self.record = dict(DisplayName='Demo',DisplayIcon='',UninstallString=f'"{self.exe}"',
            InstallLocation=str(self.root),InstallDate='20261024',
            _source=dict(hive='HKCU',view=64,subkey='compression-fixture'))
        self.registry.put('HKCU',inventory.UNINSTALL+'\\compression-fixture',
            {key:self.record.get(key,'') for key in inventory.FIELDS})
        self.patches = [patch.object(core,'DATA_DIR',self.base/'state'),
            patch.dict(inventory._ISSUED,{},clear=True),
            patch.object(inventory,'winreg',self.registry),
            patch.object(compression.windows,'filesystem',return_value='NTFS'),
            patch.object(compression.windows,'administrator',return_value=True)]
        for p in self.patches:p.start()
        with compression._LOCK:compression._CATALOG.clear()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def scan(self, records=None):
        return compression.scan(records=[self.record] if records is None else records,icons=False)
    def item(self):
        result = self.scan();self.assertFalse(result['errors'])
        self.assertEqual(len(result['items']),1)
        return result['items'][0]
    def noop(self,paths,mode):return dict(returncode=0,output='')

    def test_inventory_sizes_order_ntfs_filter_and_no_uninstall_capability_mutation(self):
        inventory._ISSUED['sentinel']=('keep',{})
        item=self.item()
        self.assertEqual(item.size,(self.exe.stat().st_size+self.file.stat().st_size)//1024*1024)
        self.assertEqual(item.files,2);self.assertIsNone(item.after)
        self.assertIn('sentinel',inventory._ISSUED)
        with patch.object(compression.windows,'filesystem',return_value='FAT32'):
            self.assertFalse(self.scan()['items'])
        self.assertFalse((self.base/'state').exists(),'Scan must not write settings/history')

    def test_empty_and_windows_directories_are_not_compressible_apps(self):
        with patch.dict(os.environ,{'SystemRoot':str(self.root)}):
            self.assertFalse(self.scan()['items'])
        measured=dict(logical=0,physical=0,compressed=False,errors=[],blocked=[],files=[])
        with patch.object(compression,'_measure',return_value=measured):
            self.assertFalse(self.scan()['items'])

    def test_protected_apps_are_excluded_from_inventory_and_operation_capability(self):
        measured=compression._measure(self.root)
        cases=[('protected root',patch.object(compression,'_guard',side_effect=ValueError('protected'))),
            ('shared files',patch.object(compression,'_measure',return_value=dict(measured,blocked=['shared hardlink']))),
            ('incomplete read',patch.object(compression,'_measure',return_value=dict(measured,errors=['access denied'])))]
        for reason,context in cases:
            with self.subTest(reason=reason),context:
                result=self.scan()
                self.assertFalse(result['items'])
                self.assertEqual(result['skipped'],1)
                self.assertFalse(compression._CATALOG)
        with patch.dict(os.environ,{'ProgramFiles':str(self.base)}), \
                patch.object(compression.windows,'administrator',return_value=False):
            result=self.scan()
            self.assertFalse(result['items']);self.assertEqual(result['skipped'],1)
            self.assertFalse(compression._CATALOG)

    def test_changed_registry_and_forged_item_do_not_start_compact(self):
        item=self.item();runner=Mock()
        with self.assertRaises(ValueError):compression.compress(replace(item,folder=str(self.base)),runner=runner)
        self.registry.data['HKCU',inventory.UNINSTALL+'\\compression-fixture',64]['DisplayName']='changed'
        with self.assertRaises(ValueError):compression.compress(item,runner=runner)
        runner.assert_not_called()

    def test_changed_and_new_files_are_not_dispatched(self):
        item=self.item();self.file.write_bytes(b'changed content')
        new=self.root/'new.bin';new.write_bytes(b'created after scan')
        called=[]
        def runner(paths,mode):called.extend(paths);return self.noop(paths,mode)
        result=compression.compress(item,runner=runner)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(called,[self.exe]);self.assertTrue(result['errors'])
        self.assertNotIn('new.bin',[entry['relative'] for entry in compression._CATALOG[item.key]['files']])

    def test_recovery_manifest_is_written_before_the_first_process(self):
        item=self.item()
        def runner(paths,mode):
            records=compression.history();self.assertEqual(len(records),1)
            self.assertTrue(records[0]['can_restore']);self.assertGreater(records[0]['pending'],0)
            manifest=json.loads((compression._directory()/(records[0]['id']+'.json')).read_text('utf-8'))
            self.assertEqual(len(manifest['files']),2)
            raise OSError('fixture process failure')
        result=compression.compress(item,runner=runner)
        self.assertEqual(result['status'],'failed')
        self.assertTrue(compression.history()[0]['can_restore'])

    def test_cancel_before_start_does_not_run_or_create_history(self):
        item=self.item();cancel=threading.Event();cancel.set();runner=Mock()
        result=compression.compress(item,cancel,runner=runner)
        self.assertEqual(result['status'],'cancelled');runner.assert_not_called()
        self.assertFalse(compression.history())

    def test_cancel_during_batch_retains_a_recovery_record(self):
        item=self.item();cancel=threading.Event()
        def runner(paths,mode):cancel.set();return self.noop(paths,mode)
        result=compression.compress(item,cancel,runner=runner)
        self.assertEqual(result['status'],'cancelled')
        self.assertTrue(compression.history()[0]['can_restore'])

    def test_nonzero_process_result_is_not_reported_as_success(self):
        result=compression.compress(self.item(),runner=lambda *a:dict(returncode=1,output='access denied'))
        self.assertEqual(result['status'],'partial');self.assertIn('access denied',result['errors'])

    def test_restore_uses_persisted_record_and_never_new_files(self):
        result=compression.compress(self.item(),runner=self.noop)
        record=compression.history()[0]
        new=self.root/'new.bin';new.write_bytes(b'keep')
        dispatched=[]
        def runner(paths,mode):self.assertFalse(mode);dispatched.extend(paths);return self.noop(paths,mode)
        restored=compression.restore(dict(record,install_path=str(self.base)),runner=runner)
        self.assertEqual(restored['status'],'success')
        self.assertNotIn(new,dispatched)
        self.assertEqual(len(compression.history()),1)
        self.assertTrue(compression.history()[0]['restored'])
        with self.assertRaises(ValueError):compression.restore(record,runner=runner)

    def test_restore_preserves_changed_files_and_replaced_root(self):
        compression.compress(self.item(),runner=self.noop)
        record=compression.history()[0]
        self.file.write_bytes(b'updated by application')
        dispatched=[]
        restored=compression.restore(record,runner=lambda paths,mode:(dispatched.extend(paths) or self.noop(paths,mode)))
        self.assertEqual(restored['status'],'partial');self.assertNotIn(self.file,dispatched)
        rootstat=self.root.stat()
        with patch.object(compression,'_guard',return_value=self.root), \
                patch.object(Path,'stat',return_value=SimpleNamespace(st_dev=rootstat.st_dev,st_ino=rootstat.st_ino+1)):
            with self.assertRaises(ValueError):compression.restore(record,runner=self.noop)

    def test_current_row_restore_reuses_its_compression_record(self):
        result=compression.compress(self.item(),runner=self.noop)
        result=compression.restore(result['item'],runner=self.noop)
        self.assertEqual(result['status'],'success');self.assertEqual(len(compression.history()),1)
        self.assertTrue(compression.history()[0]['restored'])

    def test_history_write_failure_prevents_file_changes(self):
        item=self.item();runner=Mock()
        with patch.object(compression,'_atomic',side_effect=PermissionError('cannot save history')):
            with self.assertRaises(PermissionError):compression.compress(item,runner=runner)
        runner.assert_not_called()

    def test_wof_reparse_files_are_not_path_links(self):
        wof=SimpleNamespace(st_mode=stat.S_IFREG,st_file_attributes=0x400,st_reparse_tag=compression.WOF_TAG)
        with patch.object(Path,'lstat',return_value=wof):self.assertFalse(core.is_link(self.file))
        wof.st_reparse_tag=0xA000000C
        with patch.object(Path,'lstat',return_value=wof):self.assertTrue(core.is_link(self.file))

    @unittest.skipUnless(os.name=='nt','Windows transparent compression')
    def test_real_compact_and_record_restore_preserve_workspace_sample_hashes(self):
        item=self.item();hashes={path.name:core.sha256_file(path) for path in (self.exe,self.file)}
        outcomes=[]
        def runner(paths,mode):
            outcome=compression._compact(paths,mode)
            outcome['attributes']=[(str(path),path.lstat().st_file_attributes,path.lstat().st_reparse_tag) for path in paths]
            outcomes.append(outcome)
            return outcome
        result=compression.compress(item,runner=runner)
        self.assertEqual(result['status'],'success',result['errors'])
        self.assertTrue(result['item'].compressed,repr(dict(outcomes=outcomes,result=result)))
        self.assertLess(result['after'],result['before'])
        self.assertEqual({path.name:core.sha256_file(path) for path in (self.exe,self.file)},hashes)
        # The common file walker must continue to see transparent WOF payloads.
        self.assertEqual(len(list(core.walk_files(self.root))),2)
        self.assertFalse(self.scan()['items'],'Managed compressed apps belong in compression history after rescan')
        result=compression.restore(compression.history()[0])
        self.assertEqual(result['status'],'success',result['errors'])
        self.assertFalse(result['item'].compressed)
        self.assertEqual({path.name:core.sha256_file(path) for path in (self.exe,self.file)},hashes)


if __name__=='__main__':unittest.main()
