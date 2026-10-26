"""Snapshot handoff guards without a GUI or native actions."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from cplus_cleaner.common import core
from cplus_cleaner.features.home import home_results
from cplus_cleaner.features.popup import popup
from cplus_cleaner.features.privacy import privacy
from tests.test_popup_huorong import vendor_rule


class Value:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value


class HomeResultTests(unittest.TestCase):
    def test_failed_missing_and_malformed_results_do_not_create_a_page(self):
        app = SimpleNamespace(busy=False, ensure_view=Mock())
        for key, feature in (("missing", dict(payload={})),
                             ("large", dict(payload=None)),
                             ("large", dict(payload=dict(items=[]), error="read failed")),
                             ("cdrive", dict(payload=dict(items=[]))),
                             ("privacy", dict(payload={})),
                             ("popup", None)):
            with self.subTest(key=key, feature=feature):
                self.assertFalse(home_results.apply_result(app, key, feature))
        app.ensure_view.assert_not_called()

    def test_active_application_or_page_keeps_existing_work_and_pending_scan(self):
        page = SimpleNamespace(scanning=True, processing=False, queue_active=False,
                               _destroyed=False, _pending_open="pending", after_cancel=Mock())
        app = SimpleNamespace(busy=True, ensure_view=Mock(return_value=page))
        feature = dict(payload=dict(items=[]))
        self.assertFalse(home_results.apply_result(app, "popup", feature))
        app.ensure_view.assert_not_called()
        app.busy = False
        self.assertFalse(home_results.apply_result(app, "popup", feature))
        page.after_cancel.assert_not_called()
        self.assertEqual(page._pending_open, "pending")

    def test_privacy_preserves_session_and_record_identity_for_later_cleanup(self):
        session = privacy.Session()
        record = dict(id="fixture-record", kind="registry", category="run")
        session.categories["run"].records.append(record)
        session.scanned = session.cancelled = True
        page = SimpleNamespace(scanning=False, _pending_open=None, clean_errors=["old"],
                               build_groups=Mock(), update_totals=Mock(), status=Value())
        app = SimpleNamespace(busy=False, ensure_view=Mock(return_value=page))
        result = dict(payload=session, cancelled=True, message="扫描已取消，保留已找到的结果")
        self.assertTrue(home_results.apply_result(app, "privacy", result))
        self.assertIs(page.session, session)
        self.assertIs(page.session.categories["run"].records[0], record)
        self.assertEqual(page.session.total_count, 1)
        self.assertEqual(page.clean_errors, [])
        self.assertIn("取消", page.status.value)

    def test_popup_adoption_enables_results_and_cancels_pending_scan(self):
        with tempfile.TemporaryDirectory(prefix="home-handoff-", dir=core.APP_DIR.parent) as folder, \
             patch.object(core, "DATA_DIR", Path(folder)), \
             patch.object(popup.windows, "snapshot") as observations, \
             patch.object(popup.windows, "post_close") as close:
            blocker = popup.Blocker()
            blocker.state.update(enabled=True, software={"example": False}, rules={"rule": False}, counts={"rule": 7})
            saved = deepcopy(blocker.state)
            item = popup.Software("example", "Example", (folder,),
                                  (vendor_rule(identity="rule"),),source="huorong")
            page = SimpleNamespace(scanning=False, _destroyed=False, _pending_open="pending",
                                   after_cancel=Mock(), blocker=blocker, render=Mock(),
                                   finish_scan_progress=Mock(), note=Value(), _reload_history=Mock())
            app = SimpleNamespace(busy=False, ensure_view=Mock(return_value=page))
            result = dict(payload=dict(items=[item], errors=[]), cancelled=False, message="扫描完成")
            self.assertTrue(home_results.apply_result(app, "popup", result))
            self.assertEqual(blocker.items, [item])
            self.assertTrue(blocker.group_enabled(item))
            self.assertTrue(blocker.rule_enabled(item.rules[0]))
            self.assertEqual(blocker.state['counts'],saved['counts'])
            self.assertTrue(blocker.enabled)
            page.after_cancel.assert_called_once_with("pending")
            self.assertIsNone(page._pending_open)
            observations.assert_not_called()
            close.assert_not_called()
            self.assertTrue((Path(folder)/"popup/state.json").exists())


if __name__ == "__main__":
    unittest.main()

# Purpose: Regression tests for home scan result handoff
