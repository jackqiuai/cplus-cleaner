"""Persistent popup observations restore without registry discovery or policy changes."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.features.popup import popup
from tests.popup_fixtures import definition


class PopupCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='popup-cache-', dir=core.APP_DIR.parent)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.folder = self.base/'Demo'
        self.folder.mkdir()
        self.template = definition('Demo', 'demo', count=2)
        roots = (str(self.folder),)
        self.group = replace(self.template, roots=roots,
                             rules=tuple(popup.huorong.bind_rule(rule, roots) for rule in self.template.rules),
                             icon=(core.APP_DIR/'assets/app-icon.png').read_bytes())
        for target, name, value in ((core, 'DATA_DIR', self.base/'data'),
                                    (popup, 'catalog', lambda: [self.template]),
                                    (popup.windows, 'vendor_support_reason', lambda: '')):
            replacement = patch.object(target, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.path = self.base/'data/popup/results.json'

    def test_restart_restores_icons_roots_and_disabled_rule_without_discovery(self):
        blocker = popup.Blocker()
        blocker.set_items([self.group])
        blocker.enable_detected()
        blocker.set_rule(self.group.rules[0], False)
        state_path = self.path.with_name('state.json')
        saved_state = state_path.read_bytes()
        popup.save_results([self.group])
        with patch.object(popup.inventory, 'registry_records') as registry, \
                patch.object(popup.huorong, 'discover') as discover, \
                patch.object(popup, '_vendor_icon') as extract:
            restored = popup.load_results()
            registry.assert_not_called()
            discover.assert_not_called()
            extract.assert_not_called()
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].roots, self.group.roots)
        self.assertEqual(restored[0].icon, self.group.icon)
        fresh = popup.Blocker()
        fresh.set_items(restored)
        self.assertTrue(fresh.enabled)
        self.assertEqual([fresh.rule_enabled(rule) for rule in restored[0].rules], [False, True])
        self.assertEqual(state_path.read_bytes(), saved_state)

    def test_cached_fields_cannot_override_current_catalog_rules(self):
        popup.save_results([self.group])
        saved = json.loads(self.path.read_text('utf-8'))
        saved['groups'][0].update(name='Spoofed', action='kill', definition={'scan': {}})
        saved['groups'][0]['rules'].append('invented-rule')
        self.path.write_text(json.dumps(saved), encoding='utf-8')
        updated = replace(self.template, rules=self.template.rules[1:])
        with patch.object(popup, 'catalog', return_value=[updated]):
            restored = popup.load_results()
        self.assertEqual(restored[0].name, 'Demo')
        self.assertEqual([rule.id for rule in restored[0].rules], [updated.rules[0].id])
        self.assertEqual(restored[0].rules[0].action, 'close')
        self.assertIsNotNone(restored[0].rules[0].matcher)

    def test_missing_empty_and_invalid_cache_do_not_discover_software(self):
        with patch.object(popup, 'catalog') as catalog:
            self.assertIsNone(popup.load_results())
            popup.save_results([])
            self.assertEqual(popup.load_results(), [])
            catalog.assert_not_called()
        for data in ('{broken', '{}', '[]', '{"version":1,"groups":[{}]}'):
            self.path.write_text(data, encoding='utf-8')
            with self.subTest(data=data), self.assertRaises(ValueError):
                popup.load_results()

    def test_deleted_groups_unsafe_roots_and_unsupported_rules_are_omitted(self):
        popup.save_results([self.group])
        with patch.object(popup, 'catalog', return_value=[]):
            self.assertEqual(popup.load_results(), [])
        with patch.object(popup.windows, 'vendor_support_reason', return_value='unsupported'):
            self.assertEqual(popup.load_results(), [])
        saved = json.loads(self.path.read_text('utf-8'))
        saved['groups'][0]['roots'] = [self.base.anchor, r'\\server\share\Demo', str(self.base/'removed')]
        self.path.write_text(json.dumps(saved), encoding='utf-8')
        self.assertEqual(popup.load_results(), [])

    def test_failed_cache_write_preserves_previous_results(self):
        popup.save_results([self.group])
        before = self.path.read_bytes()
        with patch.object(popup, '_atomic', side_effect=OSError('denied')):
            with self.assertRaises(OSError):
                popup.save_results([])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(popup.load_results()[0].key, self.group.key)


# Purpose: Regression tests for cached popup results and exact state restoration
