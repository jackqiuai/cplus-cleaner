from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock
import json
import tempfile
import threading
import unittest

from cplus_cleaner.common import core
from cplus_cleaner.application import preferences


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="preferences-test-", dir=core.APP_DIR.parent)
        self.base = Path(self.directory.name)
        self.data_patch = patch.object(core, "DATA_DIR", self.base / "state")
        self.data_patch.start()

    def tearDown(self):
        self.data_patch.stop()
        self.directory.cleanup()

    def test_missing_and_corrupt_settings_disable_automatic_actions(self):
        self.assertEqual(preferences.load(), preferences.DEFAULTS)
        core.DATA_DIR.mkdir()
        for payload in ('{"scheduled_cleanup": "true"}', '[]', '{'):
            (core.DATA_DIR / "settings.json").write_text(payload, encoding="utf-8")
            self.assertEqual(preferences.load(), preferences.DEFAULTS)

    def test_save_roundtrip_and_invalid_time_preserves_existing_file(self):
        settings = dict(preferences.DEFAULTS, scheduled_cleanup=True, cleanup_time="09:05", close_to_tray=True)
        preferences.save(settings)
        self.assertEqual(preferences.load(), settings)
        for invalid in ("24:00", "12:60", "9:05", "09:00 & calc.exe"):
            with self.assertRaises(ValueError):
                preferences.save(dict(settings, cleanup_time=invalid))
            self.assertEqual(preferences.load(), settings)

    def test_registry_toggle_uses_only_own_current_user_value(self):
        import winreg
        key = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = key
        with patch.object(winreg, "CreateKeyEx", return_value=context) as create, \
                patch.object(winreg, "SetValueEx") as write, patch.object(winreg, "DeleteValue") as remove:
            preferences.set_autostart(True)
            create.assert_called_with(winreg.HKEY_CURRENT_USER, preferences.RUN_KEY, 0, winreg.KEY_SET_VALUE)
            write.assert_called_once_with(key, "CCCLEANER", 0, winreg.REG_SZ, preferences.startup_command())
            self.assertIn("--background", write.call_args.args[-1])
            preferences.set_autostart(False)
            remove.assert_called_once_with(key, "CCCLEANER")

    def test_scheduler_defers_busy_work_and_never_repeats_a_day(self):
        settings = dict(preferences.DEFAULTS, scheduled_cleanup=True, cleanup_time="10:30")
        schedule = preferences.DailyScheduler(settings, datetime(2026, 10, 23, 9))
        self.assertFalse(schedule.due(datetime(2026, 10, 23, 10, 29)))
        self.assertFalse(schedule.due(datetime(2026, 10, 23, 10, 31), busy=True))
        self.assertTrue(schedule.due(datetime(2026, 10, 23, 10, 32)))
        schedule.mark_started(datetime(2026, 10, 23, 10, 32))
        self.assertEqual(schedule.values["last_cleanup_date"], "2026-10-23")
        self.assertEqual(schedule.next_run, datetime(2026, 10, 24, 10, 30))
        self.assertFalse(schedule.due(datetime(2026, 10, 23, 11)))

    def test_enabling_after_time_waits_for_tomorrow_and_disabling_cancels_timer(self):
        settings = dict(preferences.DEFAULTS, scheduled_cleanup=True, cleanup_time="10:30")
        schedule = preferences.DailyScheduler(settings, datetime(2026, 10, 23, 12))
        self.assertEqual(schedule.next_run, datetime(2026, 10, 24, 10, 30))
        schedule.configure(dict(settings, scheduled_cleanup=False), datetime(2026, 10, 23, 12))
        self.assertFalse(schedule.due(datetime(2026, 10, 24, 11)))

    def test_persisted_day_avoids_repeat_even_after_clock_rolls_back(self):
        schedule = preferences.DailyScheduler(dict(preferences.DEFAULTS, scheduled_cleanup=True,
            cleanup_time="10:30", last_cleanup_date="2026-10-23"), datetime(2026, 10, 23, 9))
        self.assertEqual(schedule.next_run, datetime(2026, 10, 24, 10, 30))

    def test_clock_rollback_and_restart_cannot_repeat_completed_dates(self):
        schedule = preferences.DailyScheduler(dict(preferences.DEFAULTS, scheduled_cleanup=True,
            cleanup_time='10:30', last_cleanup_date='2026-10-26'), datetime(2026, 10, 23, 9))
        self.assertEqual(schedule.next_run, datetime(2026, 10, 27, 10, 30))
        for now in (datetime(2026, 10, 23, 11), datetime(2026, 10, 26, 11), datetime(2026, 10, 27, 10, 29)):
            self.assertFalse(schedule.due(now), now)
        self.assertTrue(schedule.due(datetime(2026, 10, 27, 10, 30)))
        values = dict(preferences.DEFAULTS, scheduled_cleanup=True, last_cleanup_date='9999-12-31')
        self.assertIsNone(preferences.DailyScheduler(values, datetime(2026, 10, 23, 9)).next_run)
        for date in (0, False, None):
            with self.subTest(date=date), self.assertRaises(ValueError):
                preferences.validate(dict(preferences.DEFAULTS, last_cleanup_date=date))

    def test_timer_scans_all_time_and_only_deletes_default_ordinary_types(self):
        rules, files = [], []
        for group, name in (("应用程序", "QQ"), ("临时文件", "Windows日志"),
                            ("临时文件", "最近打开的文件"), ("备份文件", "Boot备份信息")):
            root = self.base / name
            root.mkdir()
            file = root / "sample.tmp"
            file.write_bytes(name.encode("utf-8"))
            files.append(file)
            rules.append(core.Rule(f"{group} / {name}", root, group, days=0))
        with patch('cplus_cleaner.features.drive_cleanup.cdrive.build_rules', return_value=rules) as build:
            result = preferences.scheduled_cleanup(threading.Event())
        build.assert_called_once_with(days=0)
        self.assertEqual(result["deleted"], 2)
        self.assertFalse(files[0].exists())
        self.assertFalse(files[1].exists())
        self.assertTrue(files[2].exists())
        self.assertTrue(files[3].exists())

    def test_cancellation_during_scan_does_not_begin_deletion(self):
        cancel = threading.Event()
        cancel.set()
        with patch('cplus_cleaner.features.drive_cleanup.cdrive.build_rules', return_value=[]), patch.object(core, "delete_files") as delete:
            result = preferences.scheduled_cleanup(cancel)
        self.assertTrue(result["cancelled"])
        delete.assert_not_called()

    def test_log_panel_reads_all_audit_streams_in_order_and_skips_malformed(self):
        core.DATA_DIR.mkdir()
        for index, filename in enumerate(("operations.jsonl", "system-actions.jsonl", "activity.jsonl")):
            (core.DATA_DIR / filename).write_text(json.dumps(dict(time=f"2026-10-23 10:0{index}:00", action="delete", ok=True)) + "\n{bad\n", encoding="utf-8")
        records, issues = preferences.read_operations(limit=2)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["time"], "2026-10-23 10:02:00")
        self.assertEqual(len(issues), 3)

    def test_feedback_is_saved_locally_with_version_and_no_network(self):
        with self.assertRaises(ValueError):
            preferences.save_feedback("  ")
        path = preferences.save_feedback("点击后界面没有刷新\n操作步骤：开始扫描")
        self.assertTrue(core.inside(path, self.base))
        self.assertIn(preferences.VERSION, path.read_text(encoding="utf-8"))
        self.assertIn("开始扫描", path.read_text(encoding="utf-8"))

    def test_operation_deletion_removes_only_selected_duplicate_and_preserves_other_files(self):
        core.DATA_DIR.mkdir()
        record = dict(time="2026-10-24 03:19:56", action="software_restore", ok=True, summary="示例：success")
        line = (json.dumps(record, ensure_ascii=False)+"\r\n").encode("utf-8")
        log = core.DATA_DIR / "activity.jsonl"
        log.write_bytes(line+line+b"{incomplete\n")
        untouched = {}
        for name in ("operations.jsonl", "system-actions.jsonl", "settings.json", "software-migrations.jsonl", "compression/records.json"):
            path = core.DATA_DIR / name
            path.parent.mkdir(parents=True, exist_ok=True)
            untouched[path] = line
            path.write_bytes(line)
        records, _ = preferences.read_operations()
        selected = [row for row in records if row._origin[0] == "activity.jsonl"][1]
        log.write_bytes(log.read_bytes()+b'{"action":"settings","ok":true}\n')
        result = preferences.delete_operations([selected, selected])
        self.assertEqual(result, dict(deleted=1, errors=[]))
        self.assertEqual(log.read_bytes(), line+b'{incomplete\n{"action":"settings","ok":true}\n')
        for path, data in untouched.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertFalse(list(core.DATA_DIR.glob("*.tmp")))

    def test_operation_deletion_rejects_stale_and_untrusted_rows(self):
        preferences.append_activity("delete", ok=True, summary="original")
        records, _ = preferences.read_operations()
        target = core.DATA_DIR / "activity.jsonl"
        changed = b'{"action":"settings","summary":"changed"}\n'
        target.write_bytes(changed)
        result = preferences.delete_operations(records)
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(result["errors"])
        self.assertEqual(target.read_bytes(), changed)
        with self.assertRaises(ValueError):
            preferences.delete_operations([dict(records[0])])
        records[0]._origin = ("../settings.json", 0, b"unused")
        with self.assertRaises(ValueError):
            preferences.delete_operations(records)

    def test_operation_deletion_write_failure_preserves_log(self):
        preferences.append_activity("delete", ok=True)
        records, _ = preferences.read_operations()
        target = core.DATA_DIR / "activity.jsonl"
        original = target.read_bytes()
        with patch.object(Path, "replace", side_effect=PermissionError("fixture denied")):
            result = preferences.delete_operations(records)
        self.assertEqual(result["deleted"], 0)
        self.assertIn("fixture denied", result["errors"][0])
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(list(core.DATA_DIR.glob("*.tmp")))

    def test_operation_deletion_detects_write_during_replacement(self):
        preferences.append_activity("delete", ok=True)
        records, _ = preferences.read_operations()
        target = core.DATA_DIR / "activity.jsonl"
        original = target.read_bytes()
        changed = original+b'{"action":"settings","ok":true}\n'
        def concurrent_append(path):
            path.write_bytes(changed)
            return changed
        with patch.object(Path, "read_bytes", concurrent_append):
            result = preferences.delete_operations(records)
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(result["errors"])
        self.assertEqual(target.read_bytes(), changed)
        self.assertFalse(list(core.DATA_DIR.glob("*.tmp")))

    def test_clear_operations_removes_all_streams_beyond_limit_and_preserves_recovery_files(self):
        self.assertEqual(preferences.clear_operations(), dict(deleted=0, errors=[]))
        self.assertFalse(core.DATA_DIR.exists())
        core.DATA_DIR.mkdir()
        line = b'{"action":"delete","ok":true}\n'
        for filename in preferences.OPERATION_LOGS:
            (core.DATA_DIR / filename).write_bytes(line*520+b'{invalid\n')
        untouched = {}
        for name in ("settings.json", "software-migrations.jsonl", "bigfiles-history.jsonl", "compression/records.json"):
            path = core.DATA_DIR / name
            path.parent.mkdir(parents=True, exist_ok=True)
            untouched[path] = b'keep recovery data'
            path.write_bytes(untouched[path])
        self.assertEqual(len(preferences.read_operations()[0]), 500)
        result = preferences.clear_operations()
        self.assertEqual(result, dict(deleted=1563, errors=[]))
        self.assertEqual(preferences.read_operations(), ([], []))
        for filename in preferences.OPERATION_LOGS:
            self.assertEqual((core.DATA_DIR / filename).read_bytes(), b'')
        for path, data in untouched.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertFalse(list(core.DATA_DIR.glob("*.tmp")))

    def test_clear_operations_write_failure_preserves_log(self):
        preferences.append_activity("delete", ok=True)
        target = core.DATA_DIR / "activity.jsonl"
        original = target.read_bytes()
        with patch.object(Path, "replace", side_effect=PermissionError("fixture denied")):
            result = preferences.clear_operations()
        self.assertEqual(result["deleted"], 0)
        self.assertIn("fixture denied", result["errors"][0])
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(list(core.DATA_DIR.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
