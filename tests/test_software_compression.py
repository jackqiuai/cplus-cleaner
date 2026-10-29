from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from cplus_cleaner.common import core
from cplus_cleaner.features.app_compression import software_compression as compression
from cplus_cleaner.features.uninstall import software_uninstall as inventory
from test_software_uninstall import Registry


class CompressionTests(unittest.TestCase):
    def setUp(self):
        fixture_root = Path(os.environ.get('CPLUS_NATIVE_TEST_ROOT', str(core.APP_DIR.parent)))
        self.temp = tempfile.TemporaryDirectory(prefix='compression-fixture-',dir=fixture_root)
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
        self.real_filesystem = compression.windows.filesystem
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
        self.assertEqual(item.size,compression.allocated_size(self.exe)+compression.allocated_size(self.file))
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

    def test_restore_revalidates_current_changed_files_and_preserves_contents(self):
        compression.compress(self.item(),runner=self.noop)
        record=compression.history()[0]
        self.file.write_bytes(b'updated by application')
        dispatched=[]
        compressed=[True]
        def runner(paths, mode):
            dispatched.extend(paths);compressed[0]=False
            return self.noop(paths,mode)
        with patch.object(compression, '_file_compressed', side_effect=lambda path, info=None: compressed[0] and path == self.file):
            restored=compression.restore(record,runner=runner)
        self.assertEqual(restored['status'],'success',restored['errors'])
        self.assertIn(self.file,dispatched)
        self.assertEqual(self.file.read_bytes(),b'updated by application')

    def test_restore_rejects_replaced_root(self):
        compression.compress(self.item(),runner=self.noop)
        record=compression.history()[0]
        rootstat=self.root.stat()
        real_stat=Path.stat
        def replaced_root(path,*args,**kwargs):
            return (SimpleNamespace(st_dev=rootstat.st_dev,st_ino=rootstat.st_ino+1)
                    if path==self.root else real_stat(path,*args,**kwargs))
        with patch.object(compression,'_guard',return_value=self.root), \
                patch.object(Path,'stat',new=replaced_root):
            with self.assertRaises(ValueError):compression.restore(record,runner=self.noop)
        stored=compression.history()[0]
        self.assertEqual(stored['status'],'failed')
        self.assertIn('软件目录已被替换，请重新扫描',stored['last_errors'])

    def test_restore_changed_uncompressed_and_missing_files_is_a_completed_noop(self):
        compression.compress(self.item(),runner=self.noop)
        record=compression.history()[0]
        self.file.write_bytes(b'updated and already uncompressed')
        self.exe.unlink()
        runner, progress=Mock(), []
        restored=compression.restore(record,runner=runner,progress=progress.append)
        runner.assert_not_called()
        self.assertEqual(restored['status'],'success',restored['errors'])
        self.assertEqual(progress[-1]['progress'],100)
        self.assertEqual(progress[-1]['completed'],2)
        self.assertEqual(self.file.read_bytes(),b'updated and already uncompressed')
        self.assertFalse(compression.history()[0]['can_restore'])
        self.assertTrue(compression.history()[0]['restored'])
        with self.assertRaises(ValueError):compression.restore(record,runner=runner)

    def test_physical_baseline_excludes_existing_compression_and_restore_keeps_original_sizes(self):
        allocation=[1000]
        compressed=[True]
        with patch.object(compression,'allocated_size',side_effect=lambda path:allocation[0]), \
                patch.object(compression,'_file_compressed',side_effect=lambda *a:compressed[0]):
            item=self.item()
            def compress(paths,mode):
                allocation[0]=500
                return self.noop(paths,mode)
            result=compression.compress(item,runner=compress)
            self.assertEqual((result['before'],result['after'],result['saved']),(2000,1000,1000))
            record=compression.history()[0]
            self.assertEqual((record['old_size'],record['new_size'],record['saved_bytes']),(2000,1000,1000))
            def restore(paths,mode):
                allocation[0]=1500;compressed[0]=False
                return self.noop(paths,mode)
            result=compression.restore(record,runner=restore)
            self.assertEqual((result['before'],result['after'],result['saved']),(1000,3000,0))
            stored=compression.history()[0]
            self.assertEqual((stored['old_size'],stored['new_size'],stored['saved_bytes']),(2000,1000,1000))
            self.assertEqual(stored['restore_after'],3000)

    def test_verified_history_refresh_persists_restored_state_and_reenables_scan(self):
        compression.compress(self.item(),runner=self.noop)
        path=compression._directory()/'records.json'
        records=json.loads(path.read_text('utf-8'))
        records[0].update(remaining=1,current_size=1,last_errors=['old failed attempt'])
        compression._atomic(path,records)
        saved=path.read_bytes()
        rows=compression.history(refresh=True)
        self.assertEqual(rows[0]['current_size'],compression._measure(self.root)['physical'])
        self.assertFalse(rows[0]['can_restore'])
        self.assertTrue(rows[0]['restored'])
        self.assertNotEqual(path.read_bytes(),saved)
        self.assertTrue(compression.history()[0]['restored'])
        self.assertEqual(compression.history()[0]['last_errors'],[])
        stored=json.loads(path.read_text('utf-8'))[0]
        self.assertEqual((stored['old_size'],stored['new_size']),(records[0]['old_size'],records[0]['new_size']))
        self.assertEqual(len(self.scan()['items']),1)

    def test_cancelled_history_refresh_does_not_commit_a_restored_state(self):
        compression.compress(self.item(),runner=self.noop)
        record=compression.history()[0]
        record.pop('can_restore');record.update(restored=False,remaining=1)
        compression._store(record)
        before=(compression._directory()/'records.json').read_bytes()
        measured=compression._measure(self.root)
        cancel=threading.Event()
        def measure(*args):
            cancel.set();return measured
        with patch.object(compression,'_measure',side_effect=measure):
            compression.history(refresh=True,cancel=cancel)
        self.assertEqual((compression._directory()/'records.json').read_bytes(),before)

    def test_progress_reaches_end_when_changed_files_are_preserved(self):
        item=self.item();self.file.write_bytes(b'changed')
        progress=[]
        result=compression.compress(item,runner=self.noop,progress=progress.append)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(progress[-1]['progress'],100)
        self.assertEqual(progress[-1]['completed'],progress[-1]['total'])

    def test_large_files_have_their_own_native_batch(self):
        entries=[dict(relative=str(n),identity=[1,n,size,0]) for n,size in enumerate(
            [20,40,compression.LARGE_FILE_BYTES,10,compression.LARGE_FILE_BYTES+1,30])]
        batches=list(compression._batches(entries))
        self.assertEqual([[row['relative'] for row in batch] for batch in batches],
                         [['0','1'],['2'],['3'],['4'],['5']])

    def test_operation_progress_is_weighted_by_file_bytes(self):
        item=self.item();events=[]
        entries=compression._CATALOG[item.key]['files']
        expected=100*entries[0]['identity'][2]/sum(row['identity'][2] for row in entries)
        with patch.object(compression,'LARGE_FILE_BYTES',1):
            result=compression.compress(item,runner=self.noop,progress=events.append)
        first=next(row for row in events if row['completed']==1)
        self.assertAlmostEqual(first['progress'],min(99,expected))
        self.assertNotAlmostEqual(first['progress'],50)
        self.assertEqual(events[-1]['progress'],100)
        self.assertEqual(result['status'],'success')

    def test_native_activity_reaches_operation_progress_while_file_count_is_unchanged(self):
        item=self.item();events=[]
        def work(paths,mode,cancel,progress):
            progress(dict(busy=True,elapsed=2,current_file=str(paths[0]),bytes_processed=50000))
            return self.noop(paths,mode)
        with patch.object(compression,'_compact',side_effect=work):
            compression.compress(item,progress=events.append)
        activity=next(row for row in events if row.get('busy'))
        self.assertEqual(activity['completed'],0)
        self.assertEqual(activity['elapsed'],2)
        self.assertAlmostEqual(activity['progress'],100*50000/activity['bytes_total'])
        self.assertEqual(events[-1]['progress'],100)

    @unittest.skipUnless(os.name=='nt','Windows compact child monitoring')
    def test_native_io_counter_can_read_the_current_process_handle(self):
        value=compression._process_read_bytes(SimpleNamespace(_handle=-1))
        self.assertIsInstance(value,int)
        self.assertGreaterEqual(value,0)

    @unittest.skipUnless(os.name=='nt','Windows compact child monitoring')
    def test_waiting_native_child_reports_io_and_activity_without_waiting_for_batch_end(self):
        for reads in ([0,60000,120000],[None,None,None]):
            with self.subTest(reads=reads):
                process=Mock(returncode=0)
                process.communicate.side_effect=[compression.subprocess.TimeoutExpired('compact',.2)]*3+[(b'',None)]
                context=MagicMock();context.__enter__.return_value=process
                events=[]
                with patch.object(compression.subprocess,'Popen',return_value=context), \
                        patch.object(compression,'_process_read_bytes',side_effect=reads), \
                        patch.object(compression,'_file_compressed',return_value=True), \
                        patch.object(compression.time,'monotonic',side_effect=[0,1,2,3]):
                    outcome=compression._compact([self.file],True,progress=events.append)
                self.assertEqual(outcome['returncode'],0)
                self.assertEqual(len(events),3)
                self.assertTrue(all(row['busy'] for row in events))
                self.assertEqual(events[-1]['elapsed'],3)
                self.assertEqual(events[-1]['current_file'],str(self.file))
                self.assertEqual(events[-1]['bytes_processed'],reads[-1] or 0)
                process.terminate.assert_not_called()

    @unittest.skipUnless(os.name=='nt','Windows compact cancellation')
    def test_cancellation_remains_responsive_during_a_large_native_file(self):
        process=Mock(returncode=1)
        process.communicate.side_effect=[compression.subprocess.TimeoutExpired('compact',.2)]*2+[(b'',None)]
        context=MagicMock();context.__enter__.return_value=process
        cancel=threading.Event()
        with patch.object(compression.subprocess,'Popen',return_value=context), \
                patch.object(compression,'_process_read_bytes',return_value=0), \
                patch.object(compression,'_file_compressed',return_value=True):
            compression._compact([self.file],True,cancel=cancel,progress=lambda data:cancel.set())
        process.terminate.assert_called_once()

    def test_restored_history_refresh_updates_current_usage_without_enabling_restore(self):
        compression.compress(self.item(),runner=self.noop)
        compression.restore(compression.history()[0],runner=self.noop)
        self.file.write_bytes(b'new application data')
        row=compression.history(refresh=True)[0]
        self.assertEqual(row['current_size'],compression._measure(self.root)['physical'])
        self.assertTrue(row['restored'])
        self.assertFalse(row['can_restore'])

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
        self.assertEqual(self.real_filesystem(self.root), 'NTFS',
            'Native compression requires NTFS; set CPLUS_NATIVE_TEST_ROOT to an owned NTFS test workspace.')
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
        rescanned=self.scan()['items']
        self.assertEqual(len(rescanned),1)
        self.assertFalse(rescanned[0].compressed)

    @unittest.skipUnless(os.name=='nt','Windows transparent compression')
    def test_native_restore_retries_after_a_failed_attempt_processed_zero_files(self):
        compressed=compression.compress(self.item())
        self.assertEqual(compressed['status'],'success',compressed['errors'])
        failure=compression.restore(compression.history()[0],runner=Mock(side_effect=PermissionError('fixture locked log')))
        self.assertEqual(failure['status'],'failed')
        failed_record=compression.history()[0]
        self.assertEqual(failed_record['processed'],0)
        self.assertEqual(failed_record['pending'],0)
        self.assertGreater(failed_record['remaining'],0)
        self.assertTrue(failed_record['can_restore'])
        retried=compression.restore(failed_record)
        self.assertEqual(retried['status'],'success',retried['errors'])
        self.assertFalse(compression.history()[0]['can_restore'])
        self.assertTrue(compression.history()[0]['restored'])

    @unittest.skipUnless(os.name=='nt','Windows transparent compression')
    def test_native_restore_retries_a_legacy_record_with_zero_counters(self):
        compression.compress(self.item())
        record=compression._records()[0]
        record.pop('remaining',None)
        record.update(processed=0,pending=0,status='failed')
        compression._store(record)
        refreshed=compression.history(refresh=True)[0]
        self.assertTrue(refreshed['can_restore'])
        restored=compression.restore(refreshed)
        self.assertEqual(restored['status'],'success',restored['errors'])
        self.assertTrue(compression.history()[0]['restored'])

    @unittest.skipUnless(os.name=='nt','Windows transparent compression')
    def test_native_restore_handles_rotated_compressed_logs_without_changing_contents(self):
        compression.compress(self.item())
        record=compression.history()[0]
        self.file.rename(self.base/'previous-log.bin')
        self.file.write_bytes(b'rotated log content\n'*10000)
        outcome=compression._compact([self.file],True)
        self.assertEqual(outcome['returncode'],0,outcome)
        self.assertTrue(compression._file_compressed(self.file))
        digest=core.sha256_file(self.file)
        result=compression.restore(record)
        self.assertEqual(result['status'],'success',result['errors'])
        self.assertFalse(compression._file_compressed(self.file))
        self.assertEqual(core.sha256_file(self.file),digest)
        self.assertTrue(compression.history()[0]['restored'])
        self.assertEqual(len(self.scan()['items']),1)

    @unittest.skipUnless(os.name=='nt','Windows transparent compression')
    def test_restored_app_reappears_even_with_new_unmanaged_compressed_files(self):
        compression.compress(self.item())
        record=compression.history()[0]
        new=self.root/'new-log.bin';new.write_bytes(b'new log content\n'*10000)
        self.assertEqual(compression._compact([new],True)['returncode'],0)
        restored=compression.restore(record)
        self.assertEqual(restored['status'],'success',restored['errors'])
        self.assertFalse(restored['item'].compressed)
        self.assertTrue(compression._file_compressed(new),'Restore must not expand its retained path list')
        rescanned=self.scan()['items']
        self.assertEqual(len(rescanned),1)
        self.assertFalse(rescanned[0].compressed)
        self.assertIsNone(rescanned[0].after)

    @unittest.skipUnless(os.name=='nt','Windows transparent compression')
    def test_native_restore_after_app_writes_a_file_finishes_and_disables_record(self):
        item=self.item()
        result=compression.compress(item)
        self.assertEqual(result['status'],'success',result['errors'])
        record=compression.history()[0]
        before=(record['old_size'],record['new_size'])
        self.file.write_bytes(b'changed by the application\n'*500)
        updated_hash=core.sha256_file(self.file)
        progress=[]
        result=compression.restore(record,progress=progress.append)
        self.assertEqual(result['status'],'success',result['errors'])
        self.assertEqual(progress[-1]['progress'],100)
        self.assertFalse(compression.history()[0]['can_restore'])
        self.assertEqual(core.sha256_file(self.file),updated_hash)
        stored=compression.history()[0]
        self.assertEqual((stored['old_size'],stored['new_size']),before)


if __name__=='__main__':unittest.main()

# Purpose: Regression tests for transparent compression, restore, eligibility and history
