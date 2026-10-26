from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.features.drive_cleanup.cleanup_model import CleanupSession


class CleanupModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ccleanner-model-test-", dir=core.APP_DIR.parent)
        self.base = Path(self.tmp.name)
        self.assertTrue(core.inside(self.base.resolve(), core.APP_DIR.parent.resolve()))
        self.manifest = [
            dict(index=10, name="浏览器缓存", group="应用程序"),
            dict(index=11, name="临时文件", group="系统"),
            dict(index=12, name="缩略图", group="系统"),
            dict(index=13, name="分页文件", group="系统"),
        ]
        self.rules = [core.Rule("应用程序 / 浏览器缓存", self.base / "cache"),
                      core.Rule("应用程序 / 浏览器缓存", self.base / "other-cache"),
                      core.Rule("系统 / 临时文件", self.base / "temp"),
                      core.Rule("系统 / 缩略图", self.base / "thumbs")]
        self.session = CleanupSession(self.rules, self.manifest)

    def tearDown(self):
        self.tmp.cleanup()

    def candidate(self, name, data=b"cache", category="应用程序 / 浏览器缓存"):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return core.file_record(path, self.base, category)

    def test_only_supported_categories_created_default_unselected(self):
        self.assertEqual([category.key for category in self.session.categories], ["fq:10", "fq:11", "fq:12"])
        self.assertTrue(all(not category.selected for category in self.session.categories))
        self.assertTrue(all(not category.selectable for category in self.session.categories))
        self.assertEqual(self.session.total_bytes, 0)
        self.session.select_all()
        self.assertEqual(self.session.selected_items(), [])
        self.assertFalse(self.session.group_totals["系统"]["selected"])

    def test_default_constructor_uses_manifest_and_enabled_rules(self):
        with patch('cplus_cleaner.features.drive_cleanup.cdrive.build_rules', return_value=self.rules), \
                patch('cplus_cleaner.features.drive_cleanup.cdrive.manifest', return_value={"items": self.manifest}):
            session = CleanupSession()
        self.assertEqual([category.name for category in session.categories], ["浏览器缓存", "临时文件", "缩略图"])

    def test_stream_batches_aggregate_same_category_across_roots(self):
        first = self.candidate("cache/a", b"abc")
        second = self.candidate("other-cache/b", b"12345")
        self.assertEqual(self.session.consume([first]), 1)
        self.assertEqual(self.session.total_bytes, 3)
        self.assertEqual(self.session.consume(iter([second])), 1)
        category = self.session.categories[0]
        self.assertEqual(category.bytes_count, 8)
        self.assertEqual(category.file_count, 2)
        self.assertTrue(category.selectable)
        self.assertFalse(category.selected)
        self.assertEqual(self.session.selected_bytes, 0)
        self.assertEqual(self.session.total_files, 2)

    def test_duplicate_path_counted_once_across_batches_and_categories(self):
        first = self.candidate("cache/a", b"123")
        self.session.consume([first, first])
        self.session.consume([replace(first, category="系统 / 临时文件")])
        self.assertEqual(self.session.total_bytes, 3)
        self.assertEqual(self.session.total_files, 1)
        self.assertEqual(self.session.categories[0].items, [first])
        self.assertEqual(self.session.categories[1].items, [])
        if os.name == "nt":
            self.assertEqual(self.session.consume([replace(first, path=first.path.upper())]), 0)

    def test_unsupported_and_system_items_cannot_increase_total(self):
        pagefile = self.candidate("pagefile.sys", b"system", category="系统 / 分页文件")
        self.assertEqual(self.session.consume([pagefile]), 0)
        self.assertEqual(self.session.total_bytes, 0)
        with self.assertRaises(TypeError):
            self.session.consume([{"id": "pagefile", "size": 999999999}])
        self.assertEqual(self.session.total_bytes, 0)
        valid = replace(pagefile, category="系统 / 临时文件")
        self.assertEqual(self.session.consume([valid]), 1)

    def test_selection_and_group_totals_use_real_candidate_sizes(self):
        a = self.candidate("cache/a", b"123")
        b = self.candidate("temp/b", b"12345", "系统 / 临时文件")
        self.session.consume([a, b])
        self.session.set_group_selected("系统", True)
        self.assertEqual(self.session.selected_items(), [b])
        self.assertEqual(self.session.selected_bytes, 5)
        self.assertEqual(self.session.selected_count, 1)
        totals = self.session.group_totals["系统"]
        self.assertEqual(totals, dict(bytes_count=5, file_count=1, selected_bytes=5,
                                     selected_count=1, selected=True))
        self.assertEqual(list(self.session.groups), ["应用程序", "系统"])
        self.session.set_selected("fq:10", True)
        self.assertEqual(self.session.selected_bytes, 8)
        self.session.select_all(False)
        self.assertEqual(self.session.selected_count, 0)

    def test_supported_zero_byte_file_is_selectable(self):
        file = self.candidate("empty", b"")
        self.session.consume([file])
        self.session.select_all()
        self.assertTrue(self.session.categories[0].selectable)
        self.assertEqual(self.session.selected_bytes, 0)
        self.assertEqual(self.session.selected_items(), [file])

    def test_cancel_requires_no_reset_and_keeps_partial_batches(self):
        first = self.candidate("first", b"123")
        self.session.consume([first])
        # The scanner stopping after this batch leaves real partial totals intact.
        self.assertEqual(self.session.total_files, 1)
        self.assertEqual(self.session.total_bytes, 3)
        self.assertEqual(self.session.selected_count, 0)

    def test_apply_delete_result_removes_only_confirmed_missing_and_preserves_failures(self):
        successful = self.candidate("gone", b"123")
        failed = self.candidate("failed", b"12345", "系统 / 临时文件")
        self.session.consume([successful, failed])
        self.session.select_all()
        with patch('cplus_cleaner.features.drive_cleanup.cleanup_model.os.lstat', side_effect=FileNotFoundError()):
            removed = self.session.apply_delete_result(dict(deleted=1, errors=[{"path": failed.path, "error": "busy"}]))
        self.assertEqual(removed, 1)
        self.assertEqual(self.session.total_files, 1)
        self.assertEqual(self.session.total_bytes, 5)
        self.assertEqual(self.session.selected_items(), [failed])
        self.assertFalse(self.session.categories[0].selected)

    def test_refresh_preserves_permission_errors_and_existing_changed_files(self):
        first = self.candidate("inaccessible", b"123")
        second = self.candidate("changed", b"12345")
        self.session.consume([first, second])
        self.session.select_all()
        with patch('cplus_cleaner.features.drive_cleanup.cleanup_model.os.lstat', side_effect=[PermissionError("denied"), object()]):
            self.assertEqual(self.session.refresh_after_delete(), 0)
        self.assertEqual(self.session.total_bytes, 8)
        self.assertEqual(self.session.selected_items(), [first, second])


if __name__ == "__main__":
    unittest.main()

# Purpose: Regression tests for cleanup categories, selection and totals
