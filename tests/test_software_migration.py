from dataclasses import replace
from pathlib import Path
import os
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
import json

from cplus_cleaner.common import core
from cplus_cleaner.features.app_mover import software_migration as migration


class SoftwareMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="software-move-test-", dir=core.APP_DIR.parent)
        self.base = Path(self.temp.name)
        self.source, self.destination = self.base / "SampleApp", self.base / "other-volume"
        self.source.mkdir()
        self.destination.mkdir()
        (self.source / "app.exe").write_bytes(b"workspace executable sample; never execute")
        (self.source / "data.txt").write_bytes(b"original data")
        self.registry = [dict(name="Sample App", command=str(self.source / "app.exe"), location=str(self.source))]
        self.patches = [patch.object(core, "DATA_DIR", self.base / "state"),
            patch.object(core, "installed_apps", return_value=self.registry),
            patch.object(migration, "system_drive", return_value=Path(self.base.anchor)),
            patch.object(migration, "user_program_roots", return_value=[]),
            patch.object(migration.windows, "running_images", return_value=[]),
            patch.object(migration.windows, "service_images", return_value=[]),
            patch.object(migration.windows, "filesystem", return_value="NTFS"),
            patch.object(migration.windows, "extract_icon", return_value=None),
            # Both physical fixtures stay on the workspace disk. Volume identity
            # is mocked; actual copy/hash/Junction/delete operations are real.
            patch.object(migration, "same_volume", return_value=False)]
        for mock in self.patches:
            mock.start()
        self.item = self.scan()["items"][0]

    def tearDown(self):
        # Remove only links directly inside this verified workspace fixture.
        self.assertTrue(core.inside(self.base.resolve(), core.APP_DIR.parent.resolve()))
        for current in self.base.iterdir():
            if current.exists() and core.is_link(current):
                current.rmdir()
        for mock in reversed(self.patches):
            mock.stop()
        self.temp.cleanup()

    def scan(self):
        return migration.scan(threading.Event())

    def test_inventory_deduplicates_nested_and_duplicate_install_records(self):
        child = self.source / "version-1"
        child.mkdir()
        self.registry.extend([dict(self.registry[0]), dict(name="Nested", command="", location=str(child))])
        self.assertEqual([root for _, root, _ in migration.discovery()], [self.source])
        with self.assertRaises(ValueError):
            migration.eligible_path(core.APP_DIR)
        with self.assertRaises(ValueError):
            migration.eligible_path(core.APP_DIR.parent)

    def test_missing_display_icon_uses_main_application_instead_of_uninstaller(self):
        uninstall = self.source / "Uninstall.exe"
        uninstall.write_bytes(b"uninstaller icon")
        icon = migration.application_icon(self.source, None, uninstall, "app")
        self.assertEqual(icon, self.source / "app.exe")
        (self.source / "app.exe").unlink()
        nested = self.source / "Application"
        nested.mkdir()
        main = nested / "PhotoEditor.exe"
        main.write_bytes(b"application icon")
        self.assertEqual(migration.application_icon(self.source, uninstall, uninstall, "PhotoEditor"), main)

    def test_default_destination_uses_d_then_e_then_later_letters(self):
        with patch.object(migration, "system_drive", return_value=Path("C:/")), \
                patch.object(migration.windows, "fixed_drives") as drives, \
                patch.object(shutil, "disk_usage", side_effect=AssertionError("Do not rank by free space")):
            for values, expected in ((["F:/", "E:/", "D:/"], r"D:\Program Files"),
                                     (["F:/", "E:/"], r"E:\Program Files"),
                                     (["F:/"], r"F:\Program Files"), (["B:/", "C:/"], "")):
                drives.return_value = [Path(value) for value in values]
                self.assertEqual(migration.default_destination(), expected)
            drives.return_value = [Path("D:/"), Path("E:/")]
            with patch.object(migration.windows, "filesystem", side_effect=lambda path: "FAT32" if path.drive == "D:" else "NTFS"):
                self.assertEqual(migration.default_destination(), r"E:\Program Files")

    def test_old_default_is_migrated_but_explicit_custom_target_is_preserved(self):
        core.DATA_DIR.mkdir()
        target = core.DATA_DIR / "software-migration-settings.json"
        target.write_text(json.dumps(dict(destination=r"D:\CCCLEANER软件搬家")), encoding="utf-8")
        with patch.object(migration, "system_drive", return_value=Path("C:/")), \
                patch.object(migration.windows, "fixed_drives", return_value=[Path("D:/")]):
            self.assertEqual(migration.default_destination(), r"D:\Program Files")
            migration.save_destination(self.destination)
            self.assertEqual(migration.default_destination(), str(self.destination))

    @unittest.skipUnless(os.name == "nt", "Windows Junction")
    def test_progress_reports_real_scan_counts_and_chunked_copy_bytes(self):
        observations = []
        migration.scan(threading.Event(), on_progress=observations.append)
        scanned = [item for item in observations if item["stage"] == "scan"]
        self.assertEqual(scanned[-1]["completed"], scanned[-1]["total"])
        self.assertEqual(scanned[-1]["total"], 1)
        (self.source / "large.bin").write_bytes(b"x" * (5 * 1024 * 1024))
        observations.clear()
        record = migration.move(self.item, self.destination, on_progress=observations.append)
        copied = [item for item in observations if item["stage"] == "copy"]
        self.assertTrue(any(0 < item["completed"] < item["total"] for item in copied))
        self.assertEqual([item["completed"] for item in copied], sorted(item["completed"] for item in copied))
        self.assertEqual(copied[-1]["completed"], record["size"])
        self.assertEqual(copied[-1]["total"], record["size"])
        self.assertTrue({"prepare", "copy", "verify", "switch", "cleanup", "done"} <= {item["stage"] for item in observations})

    def test_scan_measures_actual_files_and_reports_running_or_service_apps(self):
        self.assertEqual(self.item.size, sum(file.stat().st_size for file in self.source.iterdir()))
        self.assertEqual(self.item.files, 2)
        with patch.object(migration.windows, "running_images", return_value=[self.source / "app.exe"]):
            self.assertIn("运行中", self.scan()["items"][0].blocked)
            with self.assertRaisesRegex(ValueError, "仍在运行"):
                migration.move(self.item, self.destination)
        with patch.object(migration.windows, "service_images", return_value=[f'"{self.source / "app.exe"}" --service']):
            with patch.object(migration, "measure", wraps=migration.measure) as measured:
                result = self.scan()
            self.assertFalse(result["items"])
            self.assertEqual(result["excluded_services"], 1)
            measured.assert_not_called()
            with self.assertRaisesRegex(ValueError, "系统服务"):
                migration.move(self.item, self.destination)

    def test_same_volume_and_nested_destination_are_rejected_without_changes(self):
        with patch.object(migration, "same_volume", return_value=True):
            with self.assertRaisesRegex(ValueError, "另一个磁盘"):
                migration.move(self.item, self.destination)
        with self.assertRaises(ValueError):
            migration.move(self.item, self.source / "nested")
        self.assertFalse(core.is_link(self.source))
        self.assertFalse(list(self.destination.iterdir()))

    @unittest.skipUnless(os.name == "nt", "Windows Junction")
    def test_verified_move_releases_old_copy_and_keeps_original_path_working(self):
        expected = core.tree_digest(self.source)
        record = migration.move(self.item, self.destination)
        self.assertTrue(record["ok"])
        self.assertEqual(record["released"], self.item.size)
        self.assertTrue(core.is_link(self.source))
        self.assertFalse(Path(record["backup"]).exists())
        self.assertEqual(core.tree_digest(self.source), expected)
        self.assertEqual(core.tree_digest(Path(record["target"])), expected)
        self.assertEqual(migration.records()[0]["status"], "搬家完成")
        self.assertEqual(migration.discovery(), [])

    def test_failed_junction_rolls_source_back_and_keeps_verified_target_copy(self):
        before = core.tree_digest(self.source)
        with patch.object(core, "create_junction", side_effect=OSError("link failed")):
            with self.assertRaisesRegex(OSError, "link failed"):
                migration.move(self.item, self.destination)
        self.assertFalse(core.is_link(self.source))
        self.assertEqual(core.tree_digest(self.source), before)
        record = migration.records()[0]
        self.assertFalse(record["ok"])
        self.assertEqual(core.tree_digest(Path(record["target"])), before)

    def test_source_changes_during_copy_abort_without_removing_source(self):
        real_copy = shutil.copytree
        def mutate(*args, **kwargs):
            result = real_copy(*args, **kwargs)
            (self.source / "data.txt").write_bytes(b"new live data")
            return result
        with patch.object(shutil, "copytree", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "复制校验失败"):
                migration.move(self.item, self.destination)
        self.assertFalse(core.is_link(self.source))
        self.assertEqual((self.source / "data.txt").read_bytes(), b"new live data")

    def test_replaced_root_is_not_admitted_using_a_stale_scan(self):
        self.source.rename(self.base / "old-root")
        self.source.mkdir()
        with self.assertRaisesRegex(ValueError, "目录已变化"):
            migration.move(self.item, self.destination)

    def test_no_copy_when_cancelled_low_space_or_audit_is_unwritable(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            migration.move(self.item, self.destination, cancel)
        with patch.object(shutil, "disk_usage", return_value=shutil._ntuple_diskusage(100, 100, 0)):
            with self.assertRaisesRegex(ValueError, "空间不足"):
                migration.move(self.item, self.destination)
        with patch.object(migration, "append_record", side_effect=OSError("audit unwritable")):
            with self.assertRaises(OSError):
                migration.move(self.item, self.destination)
        self.assertFalse(list(self.destination.iterdir()))
        self.assertTrue((self.source / "data.txt").exists())

    @unittest.skipUnless(os.name == "nt", "Windows Junction")
    def test_cleanup_failure_preserves_working_target_and_records_old_copy(self):
        with patch.object(shutil, "rmtree", side_effect=OSError("file locked")):
            record = migration.move(self.item, self.destination)
        self.assertTrue(record["ok"])
        self.assertEqual(record["released"], 0)
        self.assertIn("旧副本保留", record["status"])
        self.assertTrue(Path(record["backup"]).exists())
        self.assertTrue((self.source / "app.exe").exists())

    @unittest.skipUnless(os.name == "nt", "Windows Junction")
    def test_journal_failure_after_switch_keeps_old_copy_and_history_can_restore(self):
        write = migration.append_record
        def fail_after_switch(record):
            if core.is_link(self.source):
                raise OSError("audit became unwritable")
            return write(record)
        with patch.object(migration, "append_record", side_effect=fail_after_switch):
            record = migration.move(self.item, self.destination)
        self.assertTrue(record["ok"])
        self.assertEqual(record["released"], 0)
        self.assertTrue(Path(record["backup"]).exists())
        recovered = migration.records()[0]
        self.assertTrue(recovered["ok"])
        migration.restore(recovered)
        self.assertFalse(core.is_link(self.source))

    @unittest.skipUnless(os.name == "nt", "Windows Junction")
    def test_restore_uses_current_target_data_after_app_has_written_new_content(self):
        record = migration.move(self.item, self.destination)
        (self.source / "data.txt").write_bytes(b"new work after migration")
        restored = migration.restore(record)
        self.assertTrue(restored["restored"])
        self.assertFalse(core.is_link(self.source))
        self.assertEqual((self.source / "data.txt").read_bytes(), b"new work after migration")
        self.assertTrue(Path(record["target"]).is_dir())

    @unittest.skipUnless(os.name == "nt", "Windows Junction")
    def test_nested_links_are_skipped_and_changed_link_cannot_be_restored(self):
        extra = self.base / "outside"
        extra.mkdir()
        core.create_junction(self.source / "nested", extra)
        result = self.scan()
        self.assertFalse(result["items"])
        self.assertTrue(result["skipped"])
        (self.source / "nested").rmdir()
        record = migration.move(self.item, self.destination)
        self.source.rmdir()
        core.create_junction(self.source, extra)
        with self.assertRaisesRegex(ValueError, "联接已变化"):
            migration.restore(record)


if __name__ == "__main__":
    unittest.main()
