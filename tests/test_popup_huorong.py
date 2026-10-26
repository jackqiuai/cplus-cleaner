from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.features.popup import popup
from cplus_cleaner.features.popup import popup_huorong as h
from cplus_cleaner.features.popup.popup_models import Rule, Software


def vendor_rule(scan=None, action='close', identity='vendor-fixture'):
    scan = scan if scan is not None else {
        'procname': r'%INSTDIR%\ad.exe', 'class': 'AdPopup', 'caption': 'News',
        'styles': 0x80000000, 'exstyles': 8, 'pos': 5, 'size': '300,200'}
    raw = {'name': identity, 'scan': scan, 'treat': action}
    effective = 'close_hide' if action == '' else action
    rule = Rule(identity, '', '', '', 0, 0, -1, action == 'close', 'huorong', effective, action,
                definition=raw)
    return h.bind_rule(rule, (r'C:\Apps\Demo',))


def observed():
    return dict(hwnd=123, pid=456, created=789, image=r'C:\Apps\Demo\ad.exe', title='News',
                **{'class': 'AdPopup'}, width=450, height=300, popup=True,
                logical_width=300, logical_height=200, styles=0x80000000, exstyles=8,
                position=5, pixels={(0, 0): 0xff112233})


class HuorongRulesTests(unittest.TestCase):
    def test_retains_all_groups_occurrences_and_only_supported_close_defaults(self):
        first, second = h.catalog(), h.catalog()
        rules = [r for g in first for r in g.rules]
        self.assertEqual((len(first), len(rules), len({r.id for r in rules})), (200, 769, 769))
        self.assertEqual(sum(r.raw_treat == 'close' for r in rules), 489)
        self.assertEqual(sum(r.raw_treat == 'kill' for r in rules), 168)
        self.assertEqual([r.id for r in rules], [r.id for g in second for r in g.rules])
        self.assertTrue(all(not r.default_enabled for r in rules if r.raw_treat != 'close'))
        self.assertTrue(all(not r.default_enabled and r.matcher is None for r in rules if not r.supported))
        self.assertEqual([r.id for g in popup.catalog() for r in g.rules],[r.id for r in rules])
        original = h.asset()['groups']
        self.assertEqual([r.definition for g in first for r in g.rules], [w for g in original for w in g['windows']])

    def test_every_condition_is_required_and_missing_values_never_become_wildcards(self):
        scan = dict(vendor_rule().definition['scan'], procname_rx=r'%INSTDIR%\\ad\.exe',
                    class_rx='Ad.*', caption_rx='News', varsize='200,150,400,250',
                    include_size=['300,200', '400,300'], exclude_size=['1,1,2,2'],
                    image={'points': [{'x': 0, 'y': 0, 'color': 0xff112233, 'type': 0}]})
        rule = vendor_rule(scan)
        self.assertTrue(rule.supported, rule.reason)
        self.assertTrue(h.matches(rule, observed()))
        for field, value in [('image', r'C:\Apps\Other\ad.exe'), ('title', 'Other'), ('class', 'Other'),
                             ('position', 3), ('logical_width', 301), ('logical_height', 201),
                             ('styles', 0), ('exstyles', 0), ('pixels', {})]:
            with self.subTest(field=field):
                self.assertFalse(h.matches(rule, dict(observed(), **{field: value})))
        for field in ('styles', 'exstyles', 'position', 'logical_width', 'logical_height'):
            window = observed();del window[field]
            self.assertFalse(h.matches(rule, window))

    def test_style_bit_overlap_and_zero_match_native_condition(self):
        rule = vendor_rule()
        self.assertTrue(h.matches(rule, dict(observed(), styles=0x80000001, exstyles=24)))
        self.assertFalse(h.matches(rule, dict(observed(), styles=1)))
        scan = dict(rule.definition['scan'], styles=0, exstyles=0)
        zero = vendor_rule(scan)
        self.assertTrue(h.matches(zero, dict(observed(), styles=0, exstyles=0)))
        self.assertFalse(h.matches(zero, dict(observed(), styles=1, exstyles=0)))
        signed = vendor_rule(dict(scan, styles=-2147483648))
        self.assertTrue(signed.supported)
        self.assertTrue(h.matches(signed, dict(observed(), styles=0x80000000, exstyles=0)))
        self.assertFalse(vendor_rule(dict(scan, styles='2147483648')).supported)

    def test_size_lists_ranges_and_pixel_inequality(self):
        scan = {k:v for k,v in vendor_rule().definition['scan'].items() if k != 'size'}
        scan.update(varsize='200,150,400,250', include_size=['250,180,350,220'], exclude_size=['301,200'])
        rule = vendor_rule(scan)
        self.assertTrue(h.matches(rule, observed()))
        self.assertFalse(h.matches(rule, dict(observed(), logical_width=301)))
        self.assertFalse(h.matches(rule, dict(observed(), logical_width=200)))
        scan['image'] = {'points': [{'x':0,'y':0,'color':0xff112233,'type':1}]}
        rule = vendor_rule(scan)
        self.assertFalse(h.matches(rule, observed()))
        self.assertTrue(h.matches(rule, dict(observed(), pixels={(0,0):0xff112234})))
        self.assertFalse(h.matches(rule, dict(observed(), pixels={})))

    def test_unknown_or_malformed_constraints_disable_entire_rule(self):
        base = vendor_rule().definition['scan']
        for update in ({'future_condition':1}, {'size':'300'}, {'varsize':'400,200,100,100'},
                       {'include_size':[300,200]}, {'class_rx':'[broken'}, {'styles':True},
                       {'class_rx':'(?<=foo)bar'}, {'class_rx':'(a+)+$'}, {'pos':6},
                       {'image':{'future':1}}, {'image':{'points':[{'x':0,'y':0,'color':1}]}},
                       {'procname':'%UNKNOWN%\\ad.exe'}):
            with self.subTest(update=update):
                rule = vendor_rule(dict(base, **update))
                self.assertFalse(rule.supported)
                self.assertTrue(rule.reason)
                self.assertFalse(h.matches(rule, observed()))
        self.assertFalse(vendor_rule(action='colse').supported)

    def test_case_insensitive_literals_full_regex_and_escaped_path_variables(self):
        rule = vendor_rule()
        self.assertTrue(h.matches(rule, dict(observed(), title='nEWs', **{'class':'adpopup'})))
        scan = dict(rule.definition['scan'], procname_rx=r'%INSTDIR%\\ad\.exe')
        dotted = h.bind_rule(vendor_rule(scan), (r'C:\Apps\Demo [v1]+',))
        self.assertTrue(dotted.supported, dotted.reason)
        good = dict(observed(), image=r'C:\Apps\Demo [v1]+\ad.exe')
        self.assertTrue(h.matches(dotted, good))
        self.assertFalse(h.matches(dotted, dict(good, image=r'C:\Apps\Demo v111\ad.exe')))
        regex = vendor_rule(dict(rule.definition['scan'], class_rx='Ad'))
        self.assertFalse(h.matches(regex, observed()))
        self.assertFalse(h.matches(rule, dict(observed(), **{'class':'Notepad'})))
        dotted=vendor_rule(dict(rule.definition['scan'],caption_rx='News.*'))
        for tail in ('\r','\n','\u2028','\u2029','\U0001f600'):
            self.assertFalse(h.matches(dotted,dict(observed(),title='News'+tail)))
        escaped=vendor_rule(dict(rule.definition['scan'],caption_rx=r'\ANews'))
        self.assertFalse(escaped.supported)

    def test_group_discovery_honors_names_roots_file_conditions_and_unknowns(self):
        with tempfile.TemporaryDirectory(dir=core.APP_DIR.parent, prefix='vendor-discovery-') as folder:
            root=Path(folder);(root/'ad.exe').write_bytes(b'not executable')
            group = Software('vendor-g','Example',(),(vendor_rule(),),source='huorong',
                             definition={'name':'example','exist':{'appname':'Demo*'},'instdir':{'appname':'Demo*'}})
            records=[dict(DisplayName='Demo App',InstallLocation=str(root))]
            result=h.discover(group, records, popup._roots)
            self.assertEqual(result.roots,(str(root),))
            self.assertTrue(result.rules[0].supported)
            self.assertIsNone(h.discover(group, [dict(DisplayName='Other')], popup._roots))
            definition=dict(group.definition,exist={'file_exist':'%INSTDIR%\\missing.exe'},
                            instdir={'regval':{'regkey':'ignored','valuename':'Path'}})
            self.assertIsNone(h.discover(replace(group,definition=definition),[],popup._roots,
                                         regval_reader=lambda value: ()))
            definition=dict(group.definition,exist={'appname':'Demo*','future_check':1})
            disabled=h.discover(replace(group,definition=definition),records,popup._roots)
            self.assertFalse(disabled.rules[0].supported)
            self.assertIn('future_check',disabled.rules[0].reason)

    def test_inventory_scan_uses_only_vendor_catalog_without_observing_or_acting(self):
        vendor=Software('vendor-fixture','Demo',(),(vendor_rule(),),source='huorong',
                        definition={'name':'demo','exist':{'appname':'Demo*'},'instdir':{'appname':'Demo*'}})
        with tempfile.TemporaryDirectory(dir=core.APP_DIR.parent,prefix='vendor-scan-') as folder:
            with patch.object(popup,'catalog',return_value=[vendor]), \
                 patch.object(popup.inventory,'registry_records',return_value=([dict(DisplayName='Demo',InstallLocation=folder)],[])), \
                 patch.object(popup.software_windows,'extract_icon',return_value=None), \
                 patch.object(popup.windows,'snapshot') as snapshot, patch.object(popup.windows,'apply_vendor') as act:
                updates=[];result=popup.scan(stage_progress=updates.append)
                self.assertEqual([s.source for s in result['items']],['huorong'])
                self.assertEqual(updates[-1]['completed'],1)
                self.assertEqual(updates[-1]['total'],1)
                snapshot.assert_not_called();act.assert_not_called()


    def test_vendor_icons_resolve_file_hint_registration_fallback_and_resource_index(self):
        with tempfile.TemporaryDirectory(dir=core.APP_DIR.parent,prefix='vendor-icon-') as folder:
            root=Path(folder)
            main=root/'main.exe';main.write_bytes(b'fixture never executed')
            custom=root/'brand.dll';custom.write_bytes(b'fixture never loaded')
            record=dict(DisplayName='Demo app',InstallLocation=str(root),DisplayIcon=f'"{main}",7')
            for hint, expected in ((dict(file_path=r'%INSTDIR%\brand.dll'),(custom,0)),
                                   (dict(file_path=r'%INSTDIR%\missing.exe'),(main,7)),
                                   (dict(appname='Demo*'),(main,7))):
                with self.subTest(hint=hint):
                    definition=Software('icon-one','Demo',(),(vendor_rule(),),source='huorong',
                        definition=dict(name='Demo',exist={'appname':'Demo*'},instdir={'appname':'Demo*'},display_icon=hint))
                    other=replace(definition,key='icon-two')
                    with patch.object(popup,'catalog',return_value=[definition,other]), \
                         patch.object(popup.windows,'vendor_support_reason',return_value=''), \
                         patch.object(popup.inventory,'registry_records',return_value=([record],[])), \
                         patch.object(popup.software_windows,'extract_icon',return_value=b'png') as extract, \
                         patch.object(popup.windows,'snapshot') as snapshot, \
                         patch.object(popup.windows,'apply_vendor') as action:
                        result=popup.scan()
                        self.assertEqual([item.icon for item in result['items']],[b'png',b'png'])
                        extract.assert_called_once_with(*expected)
                        snapshot.assert_not_called();action.assert_not_called()


class HuorongBlockerTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(dir=core.APP_DIR.parent,prefix='vendor-blocker-')
        self.addCleanup(self.directory.cleanup)
        self.patch=patch.object(core,'DATA_DIR',Path(self.directory.name));self.patch.start();self.addCleanup(self.patch.stop)
        self.rules=tuple(vendor_rule(action=a,identity='fixture-'+a) for a in ('close','hide','kill',''))
        self.software=Software('vendor-group','Demo',(r'C:\Apps\Demo',),self.rules,source='huorong')
        self.blocker=popup.Blocker();self.blocker.set_items([self.software])

    def test_actions_default_enabled_bulk_preserves_rule_choices_and_explicit_api_override(self):
        b=self.blocker
        b.set_all(True)
        self.assertEqual([b.rule_enabled(r) for r in self.rules],[True,False,False,False])
        self.assertTrue(all(b.action_allowed(action) for action in ('hide','kill','close_hide')))
        b.set_all(True)
        self.assertFalse(b.rule_enabled(self.rules[2]))
        b.set_rule(self.rules[2],True)
        self.assertTrue(b.rule_enabled(self.rules[2]))
        b.set_action_allowed('kill',False)
        self.assertFalse(b.rule_enabled(self.rules[2]))
        restored=popup.Blocker();restored.set_items([self.software])
        self.assertFalse(restored.rule_enabled(self.rules[2]))
        self.assertTrue(restored.action_allowed('hide'))

    def test_completed_scan_enables_groups_and_window_actions_without_new_kill_selection(self):
        b=self.blocker
        b.set_all(False)
        b.state['counts'][self.rules[0].id]=9
        b.enable_detected()
        self.assertTrue(b.enabled)
        self.assertTrue(b.group_enabled(self.software))
        self.assertEqual([b.rule_enabled(r) for r in self.rules],[True,True,False,True])
        restored=popup.Blocker();restored.set_items([self.software])
        self.assertTrue(restored.enabled)
        self.assertEqual(restored.rule_count(self.rules[0]),9)
        self.assertEqual(restored.selected_count(),3)
        b.set_rule(self.rules[2],True)
        b.enable_detected()
        self.assertTrue(b.rule_enabled(self.rules[2]))

    def test_auto_enable_respects_action_permissions_empty_results_and_atomic_failure(self):
        b=self.blocker
        b.set_all(False);b.set_action_allowed('hide',False)
        saved=json.loads(json.dumps(b.state))
        with patch.object(popup,'_atomic',side_effect=OSError('fixture denied')):
            with self.assertRaises(OSError):b.enable_detected()
        self.assertEqual(b.state,saved)
        b.enable_detected()
        self.assertFalse(b.rule_enabled(self.rules[1]))
        self.assertFalse(b.rule_enabled(self.rules[2]))
        b.set_items([]);b.enable_detected()
        self.assertFalse(b.enabled)

    def test_visible_results_hide_unsupported_rules_and_empty_groups_without_changing_catalog(self):
        bad=replace(self.rules[0],id='hidden',supported=False,matcher=None,reason='unknown')
        mixed=replace(self.software,rules=(self.rules[0],bad))
        empty=replace(self.software,key='empty-group',rules=(bad,))
        no_matcher=replace(self.rules[0],id='no-matcher',matcher=None)
        self.assertEqual(popup.visible_items([mixed,empty,replace(empty,rules=(no_matcher,))]),
                         [replace(mixed,rules=(self.rules[0],))])
        self.assertEqual(len(mixed.rules),2)
        self.assertEqual(sum(len(g.rules) for g in popup.catalog()),769)

    def test_unsupported_saved_selection_cannot_be_bulk_or_individually_enabled(self):
        bad=replace(self.rules[0],supported=False,reason='missing field',matcher=None)
        self.blocker.set_items([replace(self.software,rules=(bad,))])
        self.blocker.state['rules'][bad.id]=True
        self.assertFalse(self.blocker.rule_enabled(bad))
        self.blocker.set_all(True)
        self.assertFalse(self.blocker.rule_enabled(bad))
        with self.assertRaises(ValueError):self.blocker.set_rule(bad,True)

    def test_optional_hide_default_action_and_kill_gates_are_independent(self):
        b=self.blocker
        for rule in self.rules[1:]:
            b.set_action_allowed(rule.action,True);b.set_rule(rule,True)
        b.set_action_allowed('hide',False)
        self.assertFalse(b.rule_enabled(self.rules[1]))
        self.assertTrue(b.rule_enabled(self.rules[2]))
        self.assertTrue(b.rule_enabled(self.rules[3]))
        b.set_all(False);b.set_all(True)
        self.assertFalse(any(b.rule_enabled(r) for r in self.rules[1:]))

    def test_match_action_rechecks_policy_and_counts_only_confirmed_disappearance(self):
        b=self.blocker;b.set_all(True)
        base={key:observed()[key] for key in popup.windows._FIELDS}
        def apply(item,action,validator,points):return validator(observed())
        with patch.object(popup.windows,'snapshot',return_value=[base]) as snap, \
             patch.object(popup.windows,'enrich',return_value=observed()), \
             patch.object(popup.windows,'apply_vendor',side_effect=apply) as act, \
             patch.object(popup.windows,'disappeared',return_value=False) as gone, \
             patch.object(popup.windows,'post_close') as legacy:
            b.poll();self.assertEqual(b.total_count,0);self.assertEqual(len(b.pending),1)
            b.poll();act.assert_called_once();legacy.assert_not_called()
            snap.return_value=[];gone.return_value=True;b.poll()
            self.assertEqual(b.total_count,1)
            self.assertEqual(b.history()[0]['source'],'huorong')
            self.assertEqual(b.history()[0]['action'],'close')
        b.pending.clear()
        def disabled(item,action,validator,points):
            b.state['enabled']=False
            return validator(observed())
        b.state['enabled']=True
        with patch.object(popup.windows,'snapshot',return_value=[base]), \
             patch.object(popup.windows,'enrich',return_value=observed()), \
             patch.object(popup.windows,'apply_vendor',side_effect=disabled):
            b.poll();self.assertFalse(b.pending)

    def test_unknown_enrichment_and_mismatching_conditions_never_request_action(self):
        self.blocker.set_all(True)
        base={key:observed()[key] for key in popup.windows._FIELDS}
        with patch.object(popup.windows,'snapshot',return_value=[base]), \
             patch.object(popup.windows,'enrich',return_value=None) as enrich, \
             patch.object(popup.windows,'apply_vendor') as act:
            self.blocker.poll();act.assert_not_called()
            enrich.return_value=dict(observed(),position=3)
            self.blocker.poll();act.assert_not_called()

    def test_v1_settings_ids_and_counts_are_preserved_with_enabled_action_defaults(self):
        path=Path(self.directory.name)/'popup'/'state.json';path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(dict(version=1,enabled=True,software={'legacy':False},rules={'old':True},
                                       counts={'old':7},history={})),encoding='utf-8')
        b=popup.Blocker()
        self.assertEqual(b.state['version'],3)
        self.assertEqual(b.state['counts']['old'],7)
        self.assertTrue(b.state['rules']['old'])
        self.assertTrue(all(b.state['actions'].values()))

    def test_v2_false_permissions_migrate_without_enabling_the_global_blocker(self):
        path=Path(self.directory.name)/'popup'/'state.json';path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(dict(version=2,enabled=False,software={},rules={},counts={'old':9},history={},
                                        actions=dict.fromkeys(('hide','kill','close_hide'),False))),encoding='utf-8')
        b=popup.Blocker()
        self.assertFalse(b.enabled)
        self.assertTrue(all(b.state['actions'].values()))
        self.assertEqual(b.total_count,9)


if __name__=='__main__':unittest.main()

# Purpose: Regression tests for huorong rule parsing and capability checks
