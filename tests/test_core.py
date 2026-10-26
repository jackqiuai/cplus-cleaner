import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import core


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ccleanner-test-", dir=core.APP_DIR.parent)
        self.base = Path(self.tmp.name)
        self.assertTrue(core.inside(self.base.resolve(), core.APP_DIR.parent.resolve()))
        self.root = self.base / "source"
        self.root.mkdir()
        self.logs = patch.object(core, "DATA_DIR", self.base / "logs")
        self.logs.start()

    def tearDown(self):
        self.logs.stop()
        self.tmp.cleanup()

    def write(self, name, data=b"hello"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def scan(self, days=0):
        return core.scan_rules([core.Rule("test", self.root, days=days)])

    def test_direct_delete_selected_only(self):
        first, second = self.write("first"), self.write("second")
        items = self.scan()
        chosen = [i for i in items if i.path == str(first)]
        result = core.delete_files(chosen)
        self.assertEqual(result["deleted"], 1)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertIn('"ok": true', (core.DATA_DIR / "operations.jsonl").read_text(encoding="utf-8"))

    def test_changed_file_is_not_deleted(self):
        file = self.write("changed")
        items = self.scan()
        file.write_bytes(b"new content")
        result = core.delete_files(items)
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(file.exists())

    def test_replaced_same_size_mtime_is_not_deleted(self):
        file = self.write("replaced")
        items = self.scan()
        old = file.stat()
        replacement = self.write("new", b"hello")
        file.unlink()
        replacement.rename(file)
        os.utime(file, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertEqual(core.delete_files(items)["deleted"], 0)

    def test_age_filter(self):
        old = self.write("old")
        self.write("recent")
        stamp = time.time() - 10 * 86400
        os.utime(old, (stamp, stamp))
        self.assertEqual([Path(i.path).name for i in self.scan(7)], ["old"])

    def test_privacy_finds_jump_lists_without_shortcuts_and_keeps_documents(self):
        automatic = self.write("AutomaticDestinations/app.automaticDestinations-ms")
        custom = self.write("CustomDestinations/app.customDestinations-ms")
        self.write("desktop.ini")
        document = self.write("important.docx")
        self.write("unrelated/app.automaticDestinations-ms")
        items = core.scan_rules(core.privacy_rules(self.root))
        self.assertEqual({i.path for i in items}, {str(automatic), str(custom)})
        shortcut = self.write("document.lnk")
        items = core.scan_rules(core.privacy_rules(self.root))
        self.assertEqual({i.path for i in items}, {str(automatic), str(custom), str(shortcut)})
        self.assertTrue(document.exists())

    def test_scan_diagnostics_explain_filtered_missing_and_unreadable(self):
        self.write("new.tmp")
        old = self.write("old.tmp")
        stamp = time.time() - 10 * 86400
        os.utime(old, (stamp, stamp))
        self.write("ignore.txt")
        rules = [core.Rule("age", self.root, days=7, suffixes=(".tmp",)),
                 core.Rule("missing", self.root / "missing")]
        details, batches = [], []
        items = core.scan_rules(rules, diagnostics=details, on_batch=batches.extend)
        self.assertEqual(items, batches)
        self.assertEqual(details[0]["scanned"], 3)
        self.assertEqual(details[0]["matched"], 1)
        self.assertEqual(details[0]["filtered_age"], 1)
        self.assertEqual(details[0]["filtered_type"], 1)
        self.assertEqual(details[1]["status"], "目录不存在")
        with patch.object(core.os, "scandir", side_effect=PermissionError("test denied")):
            details = []
            self.assertEqual(core.scan_rules(rules[:1], diagnostics=details), [])
        self.assertEqual(details[0]["errors"], 1)
        self.assertEqual(details[0]["status"], "部分无法读取")
        self.assertIn("test denied", details[0]["error_samples"][0])

    def test_streamed_batches_remain_consistent_when_cancelled(self):
        for index in range(150):
            self.write(f"{index}.tmp")
        cancel = threading.Event()
        streamed, details = [], []
        def on_batch(batch):
            streamed.extend(batch)
            cancel.set()
        items = core.scan_rules([core.Rule("stream", self.root, days=0)], cancel,
                                on_batch=on_batch, diagnostics=details)
        self.assertEqual(items, streamed)
        self.assertGreater(len(items), 0)
        self.assertLess(len(items), 150)
        self.assertEqual(details[0]["status"], "已取消")

    def test_configurable_age_zero_includes_recent_files(self):
        self.write("new.tmp")
        self.assertEqual(len(self.scan(7)), 0)
        self.assertEqual(len(self.scan(0)), 1)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root), "APPDATA": str(self.root)}):
            rules = core.default_rules(days=0)
        self.assertTrue(all(r.days == 0 for r in rules))

    def test_duplicate_roots_are_deduplicated(self):
        self.write("a")
        rule = core.Rule("test", self.root, days=0)
        self.assertEqual(len(core.scan_rules([rule, rule])), 1)

    def test_duplicate_full_content_not_name(self):
        self.write("a", b"abc")
        self.write("sub/b", b"abc")
        self.write("c", b"xyz")
        groups = core.duplicate_files(self.root)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)
        self.assertEqual(len(groups[0][0].digest), 64)

    def test_hardlinks_not_counted_as_duplicate_copies(self):
        first = self.write("a")
        os.link(first, self.root / "b")
        self.assertEqual(core.duplicate_files(self.root), [])

    def test_cancel_before_delete(self):
        self.write("a")
        event = threading.Event()
        event.set()
        self.assertEqual(core.delete_files(self.scan(), event)["deleted"], 0)

    def test_root_protection(self):
        with self.assertRaises(ValueError):
            core.check_path(Path(self.root.anchor))
        with self.assertRaises(ValueError):
            core.check_path(Path.home())
        with self.assertRaises(ValueError):
            core.check_path(core.APP_DIR / "core.py")

    def test_scope_escape(self):
        outside = self.base / "outside"
        outside.write_bytes(b"do not delete")
        item = core.file_record(outside, self.root, "forged")
        self.assertEqual(core.delete_files([item])["deleted"], 0)
        self.assertTrue(outside.exists())

    def test_large_file_minimum(self):
        self.write("small", b"abc")
        self.write("big", b"x" * 30)
        self.assertEqual([Path(i.path).name for i in core.large_files(self.root, 20)], ["big"])

    def test_move_verified_no_overwrite(self):
        source = self.write("a")
        item = core.file_record(source, self.root, "test")
        target = self.base / "destination"
        target.mkdir()
        moved = core.move_file(item, target)
        self.assertFalse(source.exists())
        self.assertEqual(moved.read_bytes(), b"hello")
        source = self.write("a", b"new")
        with self.assertRaises(ValueError):
            core.move_file(core.file_record(source, self.root, "test"), target)
        self.assertTrue(source.exists())

    def test_move_source_open_failure_removes_only_own_staging_and_allows_retry(self):
        source = self.write('locked.bin')
        item = core.file_record(source, self.root, 'test')
        destination = self.base / 'destination'
        staging = destination / 'locked.bin.ccleanner-part'
        original_open = Path.open
        def open_file(path, *args, **kwargs):
            if path == source and args and args[0] == 'rb':
                raise PermissionError('fixture: source became locked')
            return original_open(path, *args, **kwargs)
        with patch.object(Path, 'open', open_file):
            with self.assertRaises(PermissionError):
                core.move_file(item, destination)
        self.assertTrue(source.exists())
        self.assertFalse(staging.exists(), 'A failed attempt must not block retry')
        staging.write_bytes(b'pre-existing recovery copy')
        with self.assertRaises(FileExistsError):
            core.move_file(item, destination)
        self.assertEqual(staging.read_bytes(), b'pre-existing recovery copy')
        staging.unlink()
        self.assertEqual(core.move_file(item, destination).read_bytes(), b'hello')

    def test_no_chat_root_wildcard_deletion(self):
        self.write("account/database.db")
        self.assertEqual(core.chat_rules(self.root), [])
        media = self.write("account/FileStorage/Image/a.jpg")
        rules = core.chat_rules(self.root, 0)
        self.assertEqual([i.path for i in core.scan_rules(rules)], [str(media)])

    def test_digest_rechecked_before_delete(self):
        a = self.write("a", b"same")
        self.write("b", b"same")
        groups = core.duplicate_files(self.root)
        chosen = next(i for i in groups[0] if i.path == str(a))
        stamp = a.stat()
        a.write_bytes(b"diff")
        os.utime(a, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertEqual(core.delete_files([chosen])["deleted"], 0)

    @unittest.skipUnless(os.name == "nt", "Windows Junction")
    def test_junction_migration_and_restore(self):
        self.write("a")
        destination = self.base / "destination"
        destination.mkdir()
        record = core.migrate_folder(self.root, destination)
        try:
            self.assertTrue(core.is_link(self.root))
            self.assertEqual((self.root / "a").read_bytes(), b"hello")
            with self.assertRaises(ValueError):
                core.check_path(self.root / "a")
            core.restore_folder(record)
            self.assertFalse(core.is_link(self.root))
        finally:
            if self.root.exists() and core.is_link(self.root):
                self.root.rmdir()

    @unittest.skipUnless(os.name == "nt", "Windows Junction")
    def test_junction_scan_does_not_follow_target(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "important").write_bytes(b"do not delete")
        link = self.root / "junction"
        core.create_junction(link, outside)
        try:
            self.assertEqual(self.scan(), [])
        finally:
            link.rmdir()

    @unittest.skipUnless(os.name == "nt", "Windows compact")
    def test_compact_and_restore_preserves_bytes(self):
        file = self.write("compress-me.txt", b"compressible data\n" * 10000)
        digest = core.sha256_file(file)
        compressed = core.run_compact(self.root, True)
        self.assertEqual(compressed["returncode"], 0, compressed["output"])
        self.assertEqual(core.sha256_file(file), digest)
        restored = core.run_compact(self.root, False)
        self.assertEqual(restored["returncode"], 0, restored["output"])
        self.assertEqual(core.sha256_file(file), digest)

    @unittest.skipUnless(os.name == "nt", "Windows Junction")
    def test_restore_rejects_changed_target(self):
        self.write("a")
        destination = self.base / "destination"
        destination.mkdir()
        record = core.migrate_folder(self.root, destination)
        try:
            (Path(record["target"]) / "a").write_bytes(b"new work")
            with self.assertRaises(ValueError):
                core.restore_folder(record)
            self.assertEqual((self.root / "a").read_bytes(), b"new work")
        finally:
            if core.is_link(self.root):
                self.root.rmdir()


if __name__ == "__main__":
    unittest.main()
