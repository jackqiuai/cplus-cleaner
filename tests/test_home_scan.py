"""Homepage orchestration with workspace chat files and mocked native scans."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import core
import home_scan as home
import privacy


class HomeScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="home-scan-", dir=core.APP_DIR.parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assertTrue(core.inside(self.root.resolve(), core.APP_DIR.parent.resolve()))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(core, "DATA_DIR", self.root/"state"))
        self.actions = [self.stack.enter_context(patch.object(module, name, side_effect=AssertionError("read-only scan")))
                        for module, name in ((core, "delete_files"), (core, "move_file"),
                                             (home.cdrive_system, "run_action"), (home.popup, "Blocker"),
                                             (home.software_compression, "compress"),
                                             (home.software_migration, "move"),
                                             (home.software_uninstall, "uninstall"))]

    def candidate(self, name, size, category="缓存文件 / 缓存"):
        return core.Candidate(str(self.root/name), str(self.root), category, size, 1, 1, 1)

    def cleanup(self, items=(), message="扫描完成"):
        return dict(rules=[], items=list(items), system_items=[], manifest_items=None, diagnostics=[]), message

    def patch(self, target, name, **kwargs):
        return self.stack.enter_context(patch.object(target, name, **kwargs))

    def test_all_features_keep_payloads_units_order_and_unique_cleanup_bytes(self):
        shared, chat = self.candidate("shared", 7), self.candidate("chat", 11)
        order = []
        def cleanup(key, cancel, report, folders):
            order.append(key)
            return self.cleanup([shared] if key == "cdrive" else [shared, chat])
        self.patch(home, "_cleanup", side_effect=cleanup)
        def chat_scan(cancel, report):
            order.append('chat')
            return dict(items=[shared, chat], errors=[], cancelled=False)
        self.patch(home.chat_cleanup, 'scan', side_effect=chat_scan)
        session = privacy.Session()
        session.categories["run"].records = [dict(id="1"), dict(id="2")]
        session.scanned = True
        def source(key, result):
            def scan(*args, **kwargs):
                order.append(key)
                return result
            return scan
        payloads = {
            "large": dict(items=[SimpleNamespace(size=100)], cancelled=False),
            "compress": dict(items=[SimpleNamespace(size=200, blocked=""), SimpleNamespace(size=900, blocked="protected")]),
            "popup": dict(items=[SimpleNamespace(rules=(1, 2, 3))]),
            "migrate": dict(items=[SimpleNamespace(size=300, blocked=""), SimpleNamespace(size=800, blocked="running")]),
            "uninstall": dict(items=[SimpleNamespace(size=10000)], errors=[], cancelled=False)}
        for key, module in (("large", home.large_files), ("compress", home.software_compression),
                            ("popup", home.popup), ("migrate", home.software_migration),
                            ("uninstall", home.software_uninstall)):
            self.patch(module, "scan", side_effect=source(key, payloads[key]))
        self.patch(home.privacy, "scan", side_effect=source("privacy", session))
        updates = []
        result = home.scan(progress=updates.append)
        self.assertEqual(order, list(home.FEATURES))
        self.assertEqual(list(result["results"]), list(home.FEATURES))
        self.assertEqual(result["cleanup_bytes"], 18)
        self.assertEqual(result["results"]["large"]["value"], 100)
        self.assertEqual(result["results"]["compress"]["value"], 200)
        self.assertEqual(result["results"]["migrate"]["value"], 300)
        self.assertEqual((result["results"]["privacy"]["value"], result["results"]["privacy"]["unit"]), (2, "条"))
        self.assertEqual((result["results"]["popup"]["value"], result["results"]["popup"]["unit"]), (3, "项"))
        self.assertEqual((result["results"]["uninstall"]["value"], result["results"]["uninstall"]["unit"]), (1, "款"))
        for key, payload in payloads.items():
            self.assertIs(result["results"][key]["payload"], payload)
        self.assertIs(result["results"]["privacy"]["payload"], session)
        self.assertEqual([data["key"] for data in updates if data["state"] == "done"], list(home.FEATURES))
        self.assertFalse(result["cancelled"])
        for action in self.actions:
            action.assert_not_called()

    def test_cdrive_uses_default_options_single_rule_scan_and_streams_real_values(self):
        rules = [core.Rule("缓存文件 / 缓存", self.root/"cache", "缓存文件", days=0),
                 core.Rule("临时文件 / 临时文件", self.root/"temp", "临时文件", days=0),
                 core.Rule("备份文件 / 备份", self.root/"backup", "备份文件", days=0)]
        build = self.patch(home.cdrive, "build_rules", return_value=rules)
        systems = [dict(id="pagefile", size=5_100_273_664)]
        self.patch(home.cdrive_system, "read_system_items", return_value=systems)
        item = self.candidate("found", 17)
        def read(selected, cancel, progress, *, diagnostics, on_batch):
            self.assertEqual(selected, rules[:2])
            progress("正在检查缓存")
            on_batch([item])
            diagnostics.append(dict(errors=0, status="完成"))
            return [item]
        reader = self.patch(core, "scan_rules", side_effect=read)
        updates = []
        result = home.scan(enabled={"cdrive"}, progress=updates.append)
        build.assert_called_once_with(days=0)
        reader.assert_called_once()
        feature = result["results"]["cdrive"]
        self.assertEqual(feature["value"], 17)
        self.assertIs(feature["payload"]["system_items"], systems)
        running = [data for data in updates if data["state"] == "running"]
        self.assertTrue(any(data.get("value") == 17 for data in running))
        self.assertTrue(all(data["total"] is None for data in running))
        self.assertEqual(result["cleanup_bytes"], 17)

    def test_unified_chat_scan_payload_is_preserved_and_deduplicated(self):
        item = self.candidate('chat-photo', 19)
        payload = dict(items=[item, item], errors=[], cancelled=False)
        scanner = self.patch(home.chat_cleanup, 'scan', return_value=payload)
        result = home.scan(enabled={'chat'})
        feature = result['results']['chat']
        self.assertEqual(feature['value'], 19)
        self.assertIs(feature['payload'], payload)
        self.assertEqual(result['cleanup_bytes'], 19)
        scanner.assert_called_once()

    def test_cancelled_file_scan_retains_partial_snapshot_and_never_starts_next_feature(self):
        self.patch(home.cdrive, "build_rules", return_value=[])
        self.patch(home.cdrive_system, "read_system_items", return_value=[])
        future = self.patch(home.privacy, "scan")
        item, cancel = self.candidate("partial", 23), threading.Event()
        def read(rules, token, progress, *, diagnostics, on_batch):
            on_batch([item])
            token.set()
            return [item]
        self.patch(core, "scan_rules", side_effect=read)
        updates = []
        result = home.scan(cancel, updates.append, enabled={"cdrive", "privacy"})
        self.assertEqual(list(result["results"]), ["cdrive"])
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["cleanup_bytes"], 23)
        self.assertEqual(result["results"]["cdrive"]["payload"]["items"], [item])
        self.assertEqual(updates[-1]["state"], "cancelled")
        self.assertIsNone(updates[-1]["total"])
        future.assert_not_called()

    def test_feature_failure_retains_last_progress_and_other_features_continue(self):
        def broken(cancel, progress):
            progress(dict(message="已检查5个目录", completed=5, total=20))
            raise OSError("fixture denied")
        self.patch(home.large_files, "scan", side_effect=broken)
        session = privacy.Session()
        session.scanned = True
        self.patch(home.privacy, "scan", return_value=session)
        updates = []
        result = home.scan(progress=updates.append, enabled={"large", "privacy"})
        self.assertIn("fixture denied", result["results"]["large"]["error"])
        failed = next(data for data in updates if data["state"] == "failed")
        self.assertEqual((failed["completed"], failed["total"]), (5, 20))
        self.assertFalse(result["results"]["privacy"]["error"])
        self.assertIs(result["results"]["privacy"]["payload"], session)

    def test_progress_stage_changes_reset_unknown_totals_and_clamp_actual_counts(self):
        def read(cancel, *, on_progress):
            on_progress(dict(message="已检查2个软件", completed=2, total=4))
            on_progress("正在读取其他软件资料")
            on_progress(dict(message="软件检查完成", completed=9, total=4))
            return dict(items=[], cancelled=False)
        self.patch(home.software_migration, "scan", side_effect=read)
        updates = []
        home.scan(progress=updates.append, enabled={"migrate"})
        running = [data for data in updates if data["state"] == "running"]
        self.assertEqual((running[-3]["completed"], running[-3]["total"]), (2, 4))
        self.assertIsNone(running[-2]["completed"])
        self.assertIsNone(running[-2]["total"])
        self.assertEqual((running[-1]["completed"], running[-1]["total"]), (4, 4))
        self.assertEqual((updates[-1]["completed"], updates[-1]["total"]), (1, 1))

    def test_pre_cancelled_or_empty_selection_does_not_call_backends(self):
        cleanup = self.patch(home, "_cleanup")
        cancel = threading.Event()
        cancel.set()
        result = home.scan(cancel)
        self.assertEqual(result, dict(results={}, cancelled=True, cleanup_bytes=0))
        result = home.scan(enabled=set())
        self.assertEqual(result, dict(results={}, cancelled=False, cleanup_bytes=0))
        cleanup.assert_not_called()
        with self.assertRaisesRegex(ValueError, "未知首页功能"):
            home.scan(enabled={"fake"})


if __name__ == "__main__":
    unittest.main()
