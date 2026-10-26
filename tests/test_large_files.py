import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import core
import large_files as files


class LargeFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="large-fixture-", dir=core.APP_DIR.parent)
        self.base = Path(self.temp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        self.logs = patch.object(core, "DATA_DIR", self.base / "state")
        self.logs.start()
        self.apps = patch.object(core, "installed_apps", return_value=[])
        self.apps.start()

    def tearDown(self):
        self.apps.stop()
        self.logs.stop()
        self.temp.cleanup()

    def write(self, name, data=b"sample-data"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def scan(self, **kwargs):
        return files.scan(root=self.root, minimum=1, blocked=[], **kwargs)["items"]

    def test_native_suffix_case_threshold_and_no_age_filter(self):
        self.write("upper.ZIP", b"x" * 10)
        self.write("archive.zip", b"x" * 10)
        self.write("installer.exe", b"x" * 9)
        self.write("unknown.bin", b"x" * 100)
        old = self.write("old.mp4", b"x" * 10)
        os.utime(old, (1, 1))
        result = files.scan(root=self.root, minimum=10, blocked=[])
        self.assertEqual({item.category for item in result["items"]}, {"压缩包", "视频文件"})
        self.assertEqual(sum(item.size for item in result["items"]), 20)

    def test_appdata_and_hardlinks_are_visible_but_links_cannot_be_operated(self):
        self.write("appdata/cache.zip")
        self.write("installed/setup.exe")
        original = self.write("original.zip")
        os.link(original, self.root / "alias.zip")
        self.write("ordinary/video.mp4")
        result = files.scan(root=self.root, minimum=1, blocked=[self.root / "installed"])
        self.assertEqual({Path(item.path).name for item in result["items"]}, {"cache.zip", "original.zip", "alias.zip", "video.mp4"})
        self.assertEqual(set(result["blocked_paths"]), {str(original), str(self.root / "alias.zip")})
        item = next(item for item in result["items"] if item.path == str(original))
        self.assertEqual(files.operate([item], "delete")["success"], [])

    def test_native_patterns_filter_directory_names_at_every_depth(self):
        for name in ("nested/mICROSOFT data/setup.exe", "nested/Program Files Clone/setup.exe",
                     "nested/sYsTeM32/setup.exe", "nested/$recycle.bin/setup.exe"):
            self.write(name)
        expected = {self.write(name) for name in ("AppData/Local/cache/setup.exe", "Windows/Logs/sample.log",
                    "ProgramData/cache/archive.zip", "registered/app.exe", "okay/Microsoft-setup.exe")}
        with patch.object(core, "protected_roots", return_value=[core.APP_DIR, self.root / "Windows"]), \
                patch.object(core, "installed_apps", return_value=[dict(location=str(self.root / "registered"))]):
            result = files.scan(root=self.root, minimum=1)
        self.assertEqual({Path(item.path) for item in result["items"]}, expected)
        self.assertIn(str(self.root / "Windows/Logs/sample.log"), result["blocked_paths"])
        self.assertIn(str(self.root / "registered/app.exe"), result["blocked_paths"])
        self.assertNotIn(str(self.root / "AppData/Local/cache/setup.exe"), result["blocked_paths"])

    def test_default_scan_uses_original_strict_30mib_boundary(self):
        for name, size in (("exact30.zip", 30*files.MIB), ("above30.zip", 30*files.MIB+1),
                           ("below50.zip", 50*files.MIB-1), ("exact50.zip", 50*files.MIB)):
            with (self.root / name).open("wb") as stream:
                stream.truncate(size)
        standard = files.scan(root=self.root, blocked=[])
        self.assertEqual({Path(item.path).name for item in standard["items"]}, {"above30.zip", "below50.zip", "exact50.zip"})
        self.assertEqual(files.MINIMUM, 30 * files.MIB + 1)

    def test_cancel_and_live_progress_have_no_fabricated_percent(self):
        self.write("file.zip")
        cancel = threading.Event()
        updates = []
        def progress(data):
            updates.append(data)
            cancel.set()
        result = files.scan(cancel, progress, root=self.root, minimum=1, blocked=[])
        self.assertTrue(result["cancelled"])
        self.assertTrue(updates)
        self.assertIsNone(updates[0]["total"])

    def test_destination_uses_first_existing_d_then_e_and_saved_custom(self):
        for drives, expected in (([Path("F:\\"), Path("D:\\"), Path("E:\\")], r"D:\bigfiles"),
                                  ([Path("C:\\"), Path("E:\\")], r"E:\bigfiles"), ([], "")):
            with patch.object(files.windows, "fixed_drives", return_value=drives), patch.object(files, "system_drive", return_value=Path("C:\\")):
                self.assertEqual(files.default_destination(), expected)
        files.save_destination(r"F:\MyBigFiles")
        self.assertEqual(files.default_destination(), r"F:\MyBigFiles")

    def test_disk_total_uses_readable_local_disks_once(self):
        drives = [Path("C:\\"), Path("D:\\")]
        with patch.object(files.windows, "fixed_drives", return_value=drives), patch.object(files.shutil, "disk_usage", side_effect=[(100, 70, 30), (200, 90, 110)]):
            # shutil.disk_usage is a named tuple on Windows.
            from collections import namedtuple
            usage = namedtuple("Usage", "total used free")
            with patch.object(files.shutil, "disk_usage", side_effect=[usage(100, 70, 30), usage(200, 90, 110)]):
                info = files.disk_inventory()
        self.assertEqual(sum(item["total"] for item in info), 300)
        self.assertEqual(sum(item["used"] for item in info), 160)

    def test_delete_only_requested_files_and_log(self):
        first, other = self.write("one.zip"), self.write("other.mp4")
        item = next(item for item in self.scan() if item.path == str(first))
        result = files.operate([item], "delete")
        self.assertEqual(result["success"], [str(first)])
        self.assertFalse(first.exists())
        self.assertTrue(other.exists())
        self.assertIn('"ok": true', (core.DATA_DIR / "operations.jsonl").read_text())

    def test_changed_file_and_new_installed_location_are_rejected(self):
        path = self.write("setup.exe")
        item = self.scan()[0]
        path.write_bytes(b"changed")
        self.assertEqual(files.operate([item], "delete")["success"], [])
        item = self.scan()[0]
        with patch.object(core, "installed_apps", return_value=[dict(location=str(self.root))]):
            self.assertEqual(files.operate([item], "delete")["success"], [])
        self.assertTrue(path.exists())

    def target(self, path):
        destination = self.base / "destination"
        destination.mkdir(exist_ok=True)
        target = destination / "C" / path.relative_to(self.root)
        return target, destination

    def test_real_copy_hash_verify_then_remove_source_and_write_history(self):
        path = self.write("folder/setup.exe", b"sample" * 10000)
        item = self.scan()[0]
        target, existing = self.target(path)
        updates = []
        with patch.object(files, "destination_path", return_value=(target, existing)):
            result = files.operate([item], "move", str(existing), progress=updates.append)
        self.assertEqual(result["success"], [str(path)])
        self.assertFalse(path.exists())
        self.assertEqual(target.read_bytes(), b"sample" * 10000)
        self.assertEqual(files.records()[0]["status"], "搬家成功")
        self.assertTrue(any(update["stage"] == "copy" for update in updates))
        self.assertTrue(any(update["stage"] == "verify" for update in updates))
        self.assertFalse(list(target.parent.glob("*.part")))

    def test_failed_verification_and_cancellation_keep_original(self):
        path = self.write("archive.zip")
        item = self.scan()[0]
        target, existing = self.target(path)
        with patch.object(files, "destination_path", return_value=(target, existing)), patch.object(core, "sha256_file", side_effect=["a", "b"]):
            result = files.operate([item], "move", str(existing))
        self.assertEqual(result["success"], [])
        self.assertTrue(path.exists())
        self.assertFalse(target.exists())
        cancel = threading.Event()
        def progress(data):
            if data["stage"] == "copy":
                cancel.set()
        with patch.object(files, "destination_path", return_value=(target, existing)):
            result = files.operate([item], "move", str(existing), cancel, progress)
        self.assertTrue(result["cancelled"])
        self.assertTrue(path.exists())
        self.assertFalse(target.exists())
        self.assertFalse(list(existing.rglob("*.part")))

    def test_refuses_same_volume_and_existing_destination_never_overwritten(self):
        path = self.write("archive.zip")
        destination = self.base / "target"
        with self.assertRaisesRegex(ValueError, "其他磁盘"):
            files.destination_path(destination, path)
        item = self.scan()[0]
        target, existing = self.target(path)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"do-not-overwrite")
        with patch.object(files, "destination_path", return_value=(target, existing)):
            result = files.operate([item], "move", str(existing))
        self.assertEqual(result["success"], [])
        self.assertTrue(path.exists())
        self.assertEqual(target.read_bytes(), b"do-not-overwrite")


if __name__ == "__main__":
    unittest.main()
