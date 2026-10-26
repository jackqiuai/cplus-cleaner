"""Recovered-rule integration; all writes/deletes stay in workspace fixtures."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cplus_cleaner.features.drive_cleanup import cdrive
from cplus_cleaner.common import core


class CDriveRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cdrive-rules-", dir=core.APP_DIR.parent)
        self.base = Path(self.tmp.name)
        self.assertTrue(core.inside(self.base.resolve(), core.APP_DIR.parent.resolve()))
        self.loc = cdrive.Locations(self.base, self.base / "Windows", self.base / "Users/test",
                                   self.base / "Users/test/AppData/Local", self.base / "Users/test/AppData/Roaming",
                                   self.base / "ProgramData", self.base / "Users/test/AppData/Local/Temp")
        self.paths = patch.object(cdrive, "locations", return_value=self.loc)
        self.paths.start()
        self.env = patch.dict(os.environ, {"SystemRoot": str(self.loc.windows), "ProgramData": str(self.loc.programdata)})
        self.env.start()
        self.logs = patch.object(core, "DATA_DIR", self.base / "logs")
        self.logs.start()
        cdrive._catalog.cache_clear()

    def tearDown(self):
        self.logs.stop()
        self.env.stop()
        self.paths.stop()
        cdrive._catalog.cache_clear()
        self.tmp.cleanup()

    def file(self, path, old=True):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"workspace rule fixture")
        if old:
            stamp = time.time() - 10 * 86400
            os.utime(path, (stamp, stamp))
        return path

    def test_default_scan_includes_recent_files_without_age_filter(self):
        recent = self.file(self.loc.local / "Temp/new.tmp", old=False)
        rules = cdrive.build_rules()
        self.assertTrue(all(r.days == 0 for r in rules))
        items = core.scan_rules(rules)
        self.assertIn(str(recent), {i.path for i in items})

    def test_recovered_rules_scan_delete_scoped_logs_and_preserve_other_files(self):
        selected = self.file(self.loc.windows / "Logs/CBS/old.LOG")
        keep = [self.file(self.loc.windows / "Logs/CBS/keep.dll"),
                self.file(self.loc.windows / "System32/driver.sys"),
                self.file(self.loc.windows / "Installer/important.msi"),
                self.file(self.loc.home / "Downloads/important.docx"),
                self.file(self.loc.home / ".nuget/NuGet/NuGet.Config"),
                self.file(self.loc.local / "Google/Chrome/User Data/Default/IndexedDB/site.db")]
        recent = self.file(self.loc.windows / "Logs/CBS/new.log", old=False)
        rules = cdrive.build_rules(days=7)
        items = core.scan_rules(rules)
        self.assertNotIn(str(selected), {i.path for i in items})
        self.assertIn(str(recent), {i.path for i in items})
        self.assertTrue(all(str(p) not in {i.path for i in items} for p in keep))
        result = core.delete_files([i for i in items if i.path == str(recent)])
        self.assertEqual(result["deleted"], 1, result)
        self.assertFalse(recent.exists())
        self.assertTrue(all(p.exists() for p in keep + [selected]))
        # The generic scanner still cannot scan/delete the entire Windows tree.
        with self.assertRaises(ValueError):
            core.check_path(self.loc.windows)

    def test_within_ranges_include_boundary_and_exclude_older_and_future_files(self):
        now = 1_780_000_000
        fixtures = {}
        for days_old in (0, 6, 7, 8, 29, 30, 31, 179, 180, 181, -1):
            path = self.file(self.loc.local / f'Temp/age-{days_old}.tmp', old=False)
            stamp = now-days_old*86400
            os.utime(path, (stamp, stamp))
            fixtures[days_old] = str(path)
        for days in (7, 30, 180):
            rules = cdrive.build_rules(days=days)
            self.assertTrue(all(rule.modified_within and rule.days == days for rule in rules))
            with patch.object(core.time, 'time', return_value=now):
                items = core.scan_rules(rules)
            self.assertEqual({item.path for item in items}, {path for age,path in fixtures.items() if 0 <= age <= days})
        with patch.object(core.time, 'time', return_value=now):
            self.assertEqual({item.path for item in core.scan_rules(cdrive.build_rules())}, set(fixtures.values()))

    def test_generic_rule_age_semantics_are_preserved(self):
        now = 1_780_000_000
        recent = self.file(self.loc.local/'Temp/recent.tmp', old=False)
        old = self.file(self.loc.local/'Temp/old.tmp', old=False)
        os.utime(recent, (now, now))
        os.utime(old, (now-10*86400, now-10*86400))
        with patch.object(core.time, 'time', return_value=now):
            items = core.scan_rules([core.Rule('generic', self.loc.local/'Temp', days=7)])
        self.assertEqual({item.path for item in items}, {str(old)})

    def test_scope_cannot_authorize_other_path_pattern_or_root(self):
        log = self.file(self.loc.windows / "Logs/old.log")
        foreign = self.file(self.loc.windows / "System32/keep.log")
        other_type = self.file(self.loc.windows / "Logs/keep.dll")
        items = core.scan_rules(cdrive.build_rules(days=0))
        item = next(i for i in items if i.path == str(log))
        forged_path = replace(core.file_record(foreign, Path(item.root), "forged"), scope_id=item.scope_id)
        forged_type = replace(core.file_record(other_type, Path(item.root), "forged"), scope_id=item.scope_id)
        forged_root = replace(item, root=str(self.loc.windows))
        invalid_id = replace(item, scope_id="not-a-real-scope")
        self.assertEqual(core.delete_files([forged_path, forged_type, forged_root, invalid_id])["deleted"], 0)
        self.assertTrue(all(p.exists() for p in [log, foreign, other_type]))

    def test_private_application_paths_and_nested_glob_are_used(self):
        qq = self.file(self.loc.roaming / "Tencent/QQ/STemp/sub/qq.tmp")
        package = self.file(self.loc.roaming / "kingsoft/wps/addons/pool/sub/cached.7z")
        keep = self.file(self.loc.roaming / "kingsoft/wps/addons/pool/sub/keep.docx")
        recovery = self.file(self.loc.roaming / "kingsoft/office6/backup/unsaved.docx")
        items = core.scan_rules(cdrive.build_rules(days=0))
        paths = {i.path for i in items}
        self.assertIn(str(qq), paths)
        self.assertIn(str(package), paths)
        self.assertNotIn(str(keep), paths)
        self.assertNotIn(str(recovery), paths)
        self.assertTrue(all(i.scope_id for i in items))

    def test_current_user_package_temp_and_no_other_user_scan(self):
        temp = self.file(self.loc.local / "Packages/Fixture.App/AC/Temp/a.tmp")
        other_user = self.file(self.base / "Users/other/AppData/Local/Temp/keep.tmp")
        items = core.scan_rules(cdrive.build_rules(days=0))
        self.assertIn(str(temp), {i.path for i in items})
        self.assertNotIn(str(other_user), {i.path for i in items})

    @unittest.skipUnless(os.name == "nt", "Windows junction API")
    def test_root_wildcard_does_not_follow_package_junction(self):
        outside = self.base / "other-app"
        keep = self.file(outside / "AC/Temp/keep.tmp")
        packages = self.loc.local / "Packages"
        packages.mkdir(parents=True)
        link = packages / "Linked.App"
        core.create_junction(link, outside)
        try:
            items = core.scan_rules(cdrive.build_rules(days=0))
            self.assertFalse(any("Linked.App" in i.path for i in items))
            self.assertTrue(keep.exists())
        finally:
            link.rmdir()

    def test_source_coverage_retains_unsupported_conditions_and_personal_data_warning(self):
        report = cdrive.coverage_report()
        self.assertEqual(report["恢复项目数"], 91)
        self.assertEqual(report["恢复路径规则数"], 197)
        audit = report["逐项支持与未实现原因"]
        self.assertEqual(audit[0]["name"], "360浏览器老版本备份")
        self.assertEqual(audit[0]["supported_paths"], [])
        self.assertEqual(audit[86]["name"], "Download目录下载的文件")
        self.assertEqual(audit[86]["supported_paths"], [])
        chrome = audit[16]
        self.assertTrue(any("IndexedDB" in str(reason) for reason in chrome["omitted"]))
        self.assertTrue(all(i["source_decoded_offset"].startswith("0x") for i in audit))


if __name__ == "__main__":
    unittest.main()
