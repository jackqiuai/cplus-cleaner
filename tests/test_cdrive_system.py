import ctypes
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cplus_cleaner.common import core
from cplus_cleaner.features.system import cdrive_system as system


class CDriveSystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ccleanner-system-test-", dir=core.APP_DIR.parent)
        self.base = Path(self.tmp.name)
        self.assertTrue(core.inside(self.base.resolve(), core.APP_DIR.parent.resolve()))
        self.logs = patch.object(core, "DATA_DIR", self.base / "logs")
        self.logs.start()

    def tearDown(self):
        self.logs.stop()
        self.tmp.cleanup()

    def read_log(self):
        return [json.loads(line) for line in (core.DATA_DIR / "system-actions.jsonl").read_text(
            encoding="utf-8").splitlines()]

    def test_read_retains_permission_error_and_does_not_perform_actions(self):
        file_stat = SimpleNamespace(st_mode=stat.S_IFREG, st_size=4096)
        with patch.object(system, "_windows_directory", return_value=r"C:\Windows"), \
                patch.object(system, "_query_recycle_bin", return_value={"size": 500, "count": 2}), \
                patch.object(system.os, "stat", side_effect=[FileNotFoundError(), PermissionError("denied"), file_stat]), \
                patch.object(system.subprocess, "run") as run, \
                patch.object(system, "_empty_recycle_bin") as empty:
            items = {item["id"]: item for item in system.read_system_items()}
        self.assertEqual(items["hibernation"]["size"], 0)
        self.assertIsNone(items["pagefile"]["size"])
        self.assertEqual(items["pagefile"]["name"], "系统虚拟内存")
        self.assertIn("denied", items["pagefile"]["status"])
        self.assertEqual(items["swapfile"]["size"], 4096)
        self.assertEqual(items["recycle_bin"]["size"], 500)
        self.assertIsNone(items["component_store"]["size"])
        self.assertIn("DISM", items["component_store"]["status"])
        run.assert_not_called()
        empty.assert_not_called()

    def test_recycle_query_failure_is_not_zero(self):
        with patch.object(system, "_windows_directory", return_value=r"C:\Windows"), \
                patch.object(system, "_file_item", side_effect=lambda key, *args: {"id": key}), \
                patch.object(system, "_query_recycle_bin", side_effect=OSError("HRESULT denied")):
            item = next(item for item in system.read_system_items() if item["id"] == "recycle_bin")
        self.assertIsNone(item["size"])
        self.assertIn("HRESULT denied", item["status"])

    def test_pagefile_size_means_actual_system_drive_file_only(self):
        file_stat = SimpleNamespace(st_mode=stat.S_IFREG, st_size=5100273664)
        with patch.object(system, "_windows_directory", return_value=r"C:\Windows"), \
                patch.object(system, "_query_recycle_bin", return_value={"size": 0, "count": 0}), \
                patch.object(system.os, "stat", return_value=file_stat) as stat_file:
            item = next(item for item in system.read_system_items() if item["id"] == "pagefile")
        self.assertEqual(item["path"], r"C:\pagefile.sys")
        self.assertEqual(item["size"], 5100273664)
        self.assertEqual(item["size"] / 1024 ** 3, 4.75)
        self.assertIn("不含其他盘", item["status"])
        self.assertEqual([call.args[0] for call in stat_file.call_args_list],
                         [r"C:\hiberfil.sys", r"C:\pagefile.sys", r"C:\swapfile.sys"])

    def test_query_uses_correct_win32_structure_and_one_drive(self):
        def fill(root, pointer):
            self.assertEqual(root, "C:\\")
            value = ctypes.cast(pointer, ctypes.POINTER(system._RecycleInfo)).contents
            self.assertEqual(value.cbSize, ctypes.sizeof(system._RecycleInfo))
            value.i64Size, value.i64NumItems = 100, 4
            return 0
        shell = SimpleNamespace(SHQueryRecycleBinW=Mock(side_effect=fill))
        with patch.object(system, "_library", return_value=shell):
            self.assertEqual(system._query_recycle_bin("C:\\"), {"size": 100, "count": 4})

    def test_admin_actions_reject_without_starting_or_elevating(self):
        with patch.object(system, "_is_admin", return_value=False), \
                patch.object(system.subprocess, "run") as run, \
                patch.object(system.subprocess, "Popen") as popen:
            for action in system.ADMIN_ACTIONS:
                result = system.run_action(action)
                self.assertFalse(result["ok"])
                self.assertIn("管理员", result["message"])
        run.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(len(self.read_log()), len(system.ADMIN_ACTIONS))

    def test_dism_failure_preserves_output_and_fixed_arguments(self):
        completed = subprocess.CompletedProcess([], 5, b"servicing denied", b"error details")
        with patch.object(system, "_is_admin", return_value=True), \
                patch.object(system, "_system_directory", return_value=r"C:\Windows\System32"), \
                patch.object(system.subprocess, "run", return_value=completed) as run:
            result = system.run_action("cleanup_component_store")
        self.assertFalse(result["ok"])
        self.assertEqual(result["returncode"], 5)
        self.assertEqual(result["stdout"], "servicing denied")
        self.assertEqual(result["stderr"], "error details")
        command = run.call_args.args[0]
        self.assertEqual(command, [r"C:\Windows\System32\Dism.exe", "/Online", "/Cleanup-Image",
                                   "/StartComponentCleanup"])
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["creationflags"], system.CREATE_NO_WINDOW)
        self.assertNotIn("/ResetBase", command)
        self.assertEqual(self.read_log()[0]["returncode"], 5)

    def test_dism_analysis_and_restart_required(self):
        completed = subprocess.CompletedProcess([], 3010, b"restart needed", b"")
        with patch.object(system, "_is_admin", return_value=True), \
                patch.object(system, "_system_directory", return_value=r"C:\Windows\System32"), \
                patch.object(system.subprocess, "run", return_value=completed) as run:
            result = system.run_action("analyze_component_store")
        self.assertTrue(result["ok"])
        self.assertTrue(result["restart_required"])
        self.assertEqual(run.call_args.args[0][-1], "/AnalyzeComponentStore")

    def test_hibernate_uses_only_fixed_powercfg_commands(self):
        completed = subprocess.CompletedProcess([], 0, b"", b"")
        with patch.object(system, "_is_admin", return_value=True), \
                patch.object(system, "_system_directory", return_value=r"C:\Windows\System32"), \
                patch.object(system.subprocess, "run", return_value=completed) as run:
            for action, setting in [("hibernate_off", "off"), ("hibernate_on", "on")]:
                self.assertTrue(system.run_action(action)["ok"])
                self.assertEqual(run.call_args.args[0], [r"C:\Windows\System32\powercfg.exe", "/hibernate", setting])

    def test_recycle_empty_is_limited_to_system_drive(self):
        with patch.object(system, "_windows_directory", return_value=r"D:\Windows"), \
                patch.object(system, "_empty_recycle_bin", return_value=0) as empty, \
                patch.object(system.subprocess, "run") as run:
            result = system.run_action("empty_recycle_bin")
        self.assertTrue(result["ok"])
        empty.assert_called_once_with("D:\\")
        run.assert_not_called()

    def test_recycle_empty_failure_retains_hresult(self):
        with patch.object(system, "_windows_directory", return_value=r"C:\Windows"), \
                patch.object(system, "_empty_recycle_bin", return_value=-2147024891):
            result = system.run_action("empty_recycle_bin")
        self.assertFalse(result["ok"])
        self.assertEqual(result["returncode"], -2147024891)
        self.assertIn("80070005", result["message"])

    def test_pagefile_settings_only_launches_windows_gui(self):
        with patch.object(system, "_system_directory", return_value=r"C:\Windows\System32"), \
                patch.object(system.subprocess, "Popen", return_value=SimpleNamespace(pid=123)) as popen, \
                patch.object(system.subprocess, "run") as run:
            result = system.run_action("pagefile_settings")
        self.assertTrue(result["ok"])
        self.assertEqual(popen.call_args.args[0], [r"C:\Windows\System32\SystemPropertiesAdvanced.exe"])
        self.assertFalse(popen.call_args.kwargs["shell"])
        run.assert_not_called()

    def test_unknown_action_is_logged_and_no_process_started(self):
        with patch.object(system.subprocess, "run") as run:
            result = system.run_action("delete_system_files")
        self.assertFalse(result["ok"])
        self.assertIn("未知", result["message"])
        run.assert_not_called()
        self.assertFalse(self.read_log()[0]["ok"])

    def test_log_creation_failure_prevents_action(self):
        file = self.base / "not-a-directory"
        file.write_text("fixture", encoding="utf-8")
        with patch.object(core, "DATA_DIR", file), \
                patch.object(system, "_empty_recycle_bin") as empty:
            result = system.run_action("empty_recycle_bin")
        self.assertFalse(result["ok"])
        self.assertIn("操作未执行", result["message"])
        empty.assert_not_called()


if __name__ == "__main__":
    unittest.main()
