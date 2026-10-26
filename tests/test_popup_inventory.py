from dataclasses import replace
from pathlib import Path
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.features.popup import popup
from cplus_cleaner.features.popup import popup_huorong as h
from cplus_cleaner.features.popup import popup_inventory as inventory
from tests.test_software_uninstall import Registry


class PopupInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=core.APP_DIR.parent, prefix='popup-index-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'Demo App'
        self.root.mkdir()
        self.values = dict(DisplayName='Demo', UninstallString=f'"{self.root / "uninstall.exe"}" /S')
        self.source = dict(hive='HKLM', view=32, subkey='demo9')

    def test_missing_or_dll_icon_and_missing_uninstaller_do_not_discard_index(self):
        for icon in ('', str(self.root/'missing.ico'), f'"{self.root / "icons.dll"}",-5'):
            item = inventory.index_record(dict(self.values, DisplayIcon=icon), self.source)
            self.assertEqual(item['_popup_root'], str(self.root))
            self.assertTrue(popup.owned(str(self.root/'ad.exe'), popup._roots(item)))
            self.assertFalse((self.root/'uninstall.exe').exists())

    def test_explicit_directory_has_priority_and_icon_directory_is_not_owned(self):
        other = Path(self.temp.name)/'Other'
        other.mkdir()
        values = dict(self.values, InstallLocation=f'"{other}"', DisplayIcon=str(self.root/'app.exe'))
        item = inventory.index_record(values, self.source)
        roots = popup._roots(item)
        self.assertTrue(popup.owned(str(other/'ad.exe'), roots))
        self.assertFalse(popup.owned(str(self.root/'ad.exe'), roots))

    def test_index_filters_native_components_updates_and_missing_required_fields(self):
        for change in (dict(SystemComponent=1), dict(ParentKeyName=''), dict(ReleaseType='Update'),
                       dict(ReleaseType='Service Pack'), dict(DisplayName=''), dict(UninstallString='')):
            self.assertIsNone(inventory.index_record(dict(self.values, **change), self.source))
        # This is not the uninstall page's list: no requirement for .ico/.exe
        # icon resources, EstimatedSize, a live uninstaller, or NoRemove.
        self.assertIsNotNone(inventory.index_record(dict(self.values, NoRemove=1), self.source))

    def test_msi_uses_product_location_and_never_owns_system_installer(self):
        product = '{12345678-1234-1234-1234-123456789abc}'
        calls = []
        def msi_reader(code, field):
            calls.append((code, field))
            return str(self.root) if field == 'InstallLocation' else str(self.root/'product.ico')
        values = dict(self.values, WindowsInstaller=1, UninstallString=f'MsiExec.exe /I{product}')
        item = inventory.index_record(values, self.source, msi_reader=msi_reader)
        self.assertEqual(item['_popup_root'], str(self.root))
        self.assertEqual({field for code,field in calls}, {'InstallLocation','ProductIcon'})
        self.assertTrue(all(code == product for code,field in calls))
        self.assertIsNone(inventory.index_record(values,self.source,msi_reader=lambda *args:''))
        self.assertIsNone(inventory.index_record(dict(values,UninstallString='msiexec.exe /X'),self.source))

    def test_four_stores_keep_same_key_in_both_views_and_both_hives(self):
        registry = Registry()
        for hive in ('HKLM','HKCU'):
            for view in (32,64):
                registry.put(hive, inventory.UNINSTALL+'\\demo9', self.values, view=view)
        records, errors = inventory.registry_records(registry=registry)
        self.assertEqual(errors, [])
        self.assertEqual([(r['_source']['view'],r['_source']['hive']) for r in records],
                         [(32,'HKLM'),(32,'HKCU'),(64,'HKLM'),(64,'HKCU')])
        event = threading.Event(); event.set()
        self.assertEqual(inventory.registry_records(event,registry=registry), ([],[]))

    def test_denied_store_is_reported_and_does_not_discard_other_stores(self):
        registry = Registry()
        registry.put('HKCU', inventory.UNINSTALL+'\\demo9', self.values, view=64)
        original = registry.OpenKey
        def open_key(hive, path, reserved=0, access=1):
            if hive == registry.HKEY_LOCAL_MACHINE:
                raise PermissionError('fixture denied')
            return original(hive,path,reserved,access)
        with patch.object(registry,'OpenKey',side_effect=open_key):
            records,errors = inventory.registry_records(registry=registry)
        self.assertEqual(len(records),1)
        self.assertEqual(len(errors),2)

    def test_command_parser_handles_spaces_env_and_rejects_relative_or_remote(self):
        with patch.dict(os.environ, POPUP_INDEX_FIXTURE=str(self.root)):
            self.assertEqual(inventory.command_image(r'"%POPUP_INDEX_FIXTURE%\remove.exe" /S'),self.root/'remove.exe')
        self.assertEqual(inventory.command_image(str(self.root/'remove.exe')+' /S'),self.root/'remove.exe')
        for command in ('uninstall.exe /S', 'msiexec.exe /X something', '"broken',
                        r'\\server\share\uninstall.exe /S', 'C:\\bad\x00.exe'):
            self.assertIsNone(inventory.command_image(command))

    def test_native_glob_is_full_value_and_key_matches_only_leaf(self):
        for pattern,value,expected in [('Demo','Demo9',False),('DEMO*','demo9',True),
                                       ('Demo?','Demo9',True),('Demo>','Demo9',True),
                                       ('Demo>',r'Demo9\Other',False),('Demo[9]','Demo9',False)]:
            self.assertEqual(inventory.name_matches(pattern,value),expected)
        record=dict(_source=dict(subkey=inventory.UNINSTALL+'\\demo9'))
        self.assertEqual(inventory.key_name(record),'demo9')

    def test_shared_system_drive_and_remote_roots_cannot_be_used_for_matching(self):
        system = Path(self.temp.name)/'Windows'
        system.mkdir()
        with patch.dict(os.environ,SystemRoot=str(system)):
            record=inventory.index_record(dict(self.values,InstallLocation=str(system)),self.source)
            self.assertEqual(popup._roots(record),set())
        for value in (self.root.anchor,r'\\server\share','relative','%UNKNOWN_POPUP_INDEX%'):
            self.assertIsNone(inventory._directory(value))

    def test_ldplayer9_uses_exact_key_version_and_only_correct_rule_group(self):
        record=inventory.index_record(dict(self.values,DisplayName='雷电模拟器9'),
                                      dict(self.source,subkey=inventory.UNINSTALL+'\\leidian9'))
        definitions=[g for g in h.catalog() if g.name=='雷电模拟器']
        original=[g.definition.copy() for g in definitions]
        results=[item for g in definitions if (item:=h.discover(g,[record],popup._roots))]
        self.assertEqual([g.definition['name'] for g in results],['dnplayer9'])
        self.assertEqual(len(results[0].rules),6)
        self.assertTrue(all(r.supported for r in results[0].rules))
        self.assertEqual([g.definition for g in definitions],original)
        nine=next(g for g in definitions if g.definition['name']=='dnplayer9')
        for changes in (dict(DisplayName='Other9'), dict(DisplayName='雷电模拟器99'),
                        dict(_source=dict(self.source,subkey='leidian')),
                        dict(_popup_root=str(self.root/'missing'))):
            self.assertIsNone(h.discover(nine,[dict(record,**changes)],popup._roots))

    def test_hide_only_group_switch_enables_window_actions_with_mocked_dispatch(self):
        record=inventory.index_record(dict(self.values,DisplayName='雷电模拟器9'),
                                      dict(self.source,subkey='leidian9'))
        group=h.discover(next(g for g in h.catalog() if g.definition['name']=='dnplayer9'),[record],popup._roots)
        with patch.object(core,'DATA_DIR',Path(self.temp.name)/'state'), \
             patch.object(popup.windows,'snapshot',return_value=[]) as observe, \
             patch.object(popup.windows,'apply_vendor') as act:
            blocker=popup.Blocker(); blocker.set_items([group]); blocker.set_software(group,True)
            self.assertTrue(blocker.group_enabled(group))
            self.assertEqual(blocker.selected_count(),6)
            blocker.poll(); act.assert_not_called()
            blocker.set_software(group,False)
            self.assertEqual(blocker.selected_count(),0)
            observe.assert_called_once()
            restored=popup.Blocker();restored.set_items([group])
            self.assertFalse(restored.group_enabled(group))

    def test_hide_fallback_never_selects_termination_or_unsupported_conditions(self):
        from tests.test_popup_huorong import vendor_rule
        from cplus_cleaner.features.popup.popup_models import Software
        hide=vendor_rule(action='hide',identity='index-hide')
        kill=vendor_rule(action='kill',identity='index-kill')
        bad=replace(hide,id='index-bad',supported=False,matcher=None,reason='unknown field')
        item=Software('index-only-optional','Demo',(r'C:\Apps\Demo',),(hide,kill,bad),source='huorong')
        with patch.object(core,'DATA_DIR',Path(self.temp.name)/'state'):
            blocker=popup.Blocker();blocker.set_items([item]);blocker.set_all(True)
            self.assertTrue(blocker.rule_enabled(hide))
            self.assertFalse(blocker.rule_enabled(kill))
            self.assertFalse(blocker.rule_enabled(bad))
            blocker.set_action_allowed('hide',False)
            blocker.set_software(item,True)
            self.assertEqual(blocker.selected_count(),0)


if __name__=='__main__':
    unittest.main()

# Purpose: Regression tests for popup software inventory and path matching
