import hashlib
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from cplus_cleaner.common import core
from cplus_cleaner.features.privacy import privacy_browser as browser


class BrowserPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="browser-privacy-fixture-", dir=core.APP_DIR.parent)
        self.root = Path(self.temp.name)
        self.processes = patch.object(browser.windows, "running_images", return_value=[])
        self.processes.start()

    def tearDown(self):
        self.processes.stop()
        self.temp.cleanup()

    @contextmanager
    def database(self, path):
        connection = sqlite3.connect(path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, name="chrome", *, relations=False):
        path = self.root / name / "Default" / "History"
        path.parent.mkdir(parents=True)
        with self.database(path) as connection:
            connection.executescript("""
                CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT, title TEXT, visit_count INTEGER, last_visit_time INTEGER);
                CREATE TABLE downloads(id INTEGER PRIMARY KEY, current_path TEXT, target_path TEXT, hash BLOB);
                CREATE TABLE visits(id INTEGER PRIMARY KEY, url INTEGER, from_visit INTEGER, opener_visit INTEGER, segment_id INTEGER, visited_link_id INTEGER);
                CREATE TABLE downloads_url_chains(id INTEGER, chain_index INTEGER, url TEXT);
                CREATE TABLE downloads_slices(download_id INTEGER, offset INTEGER, received_bytes INTEGER);
                INSERT INTO urls VALUES(1,'https://www.taobao.com/item/first','secret title',20,111);
                INSERT INTO urls VALUES(2,'https://example.invalid/keep','keep',1,112);
                INSERT INTO visits VALUES(10,1,0,0,0,0);
                INSERT INTO visits VALUES(11,2,10,10,0,0);
                INSERT INTO downloads VALUES(1,'not-on-disk.pdf','not-on-disk.pdf',X'00FF');
                INSERT INTO downloads_url_chains VALUES(1,0,'https://download.invalid/one');
                INSERT INTO downloads_slices VALUES(1,0,100);
            """)
            if relations:
                connection.executescript("""
                    CREATE TABLE keyword_search_terms(url_id INTEGER,term TEXT);
                    CREATE TABLE visit_source(id INTEGER,source INTEGER);
                    CREATE TABLE content_annotations(visit_id INTEGER,label TEXT);
                    CREATE TABLE context_annotations(visit_id INTEGER,label TEXT);
                    CREATE TABLE clusters(cluster_id INTEGER,label TEXT);
                    CREATE TABLE clusters_and_visits(cluster_id INTEGER,visit_id INTEGER);
                    CREATE TABLE cluster_keywords(cluster_id INTEGER,keyword TEXT);
                    CREATE TABLE cluster_visit_duplicates(visit_id INTEGER,duplicate_visit_id INTEGER);
                    CREATE TABLE segments(id INTEGER,url_id INTEGER);
                    CREATE TABLE segment_usage(segment_id INTEGER,count INTEGER);
                    CREATE TABLE visited_links(id INTEGER,link_url_id INTEGER);
                    CREATE TABLE edge_urls(url_id INTEGER,topics TEXT);
                    CREATE TABLE edge_visits(visit_id INTEGER,duration INTEGER);
                    CREATE TABLE edge_downloads(id INTEGER,protected_download_url TEXT);
                    CREATE TABLE edge_keyword_search_terms_source(url_id INTEGER,source INTEGER);
                    INSERT INTO keyword_search_terms VALUES(1,'private search');
                    INSERT INTO visit_source VALUES(10,1);
                    INSERT INTO content_annotations VALUES(10,'private');
                    INSERT INTO context_annotations VALUES(10,'private');
                    INSERT INTO clusters VALUES(4,'removed'),(5,'shared');
                    INSERT INTO clusters_and_visits VALUES(4,10),(5,10),(5,11);
                    INSERT INTO cluster_keywords VALUES(4,'removed'),(5,'kept');
                    INSERT INTO cluster_visit_duplicates VALUES(10,11),(11,10);
                    INSERT INTO segments VALUES(7,1);
                    INSERT INTO segment_usage VALUES(7,9);
                    INSERT INTO visited_links VALUES(8,1);
                    INSERT INTO edge_urls VALUES(1,'private');
                    INSERT INTO edge_visits VALUES(10,50);
                    INSERT INTO edge_keyword_search_terms_source VALUES(1,1);
                    INSERT INTO edge_downloads VALUES(1,'download');
                    UPDATE visits SET segment_id=7,visited_link_id=8 WHERE id=11;
                """)
        return path

    def scan(self, path, **kwargs):
        return browser.scan(roots={"chrome": path}, **kwargs)

    def test_database_failure_reports_history_and_downloads_as_failed(self):
        path = self.root/'History'
        path.write_bytes(b'corrupt database')
        result = self.scan(path)
        self.assertEqual({d['category'] for d in result['diagnostics'] if d['status']=='error'},
                         {'chrome_history', 'chrome_downloads'})
        self.assertFalse(result['items'])

    def rows(self, path, table):
        with self.database(path) as connection:
            return connection.execute(f'SELECT * FROM "{table}"').fetchall()

    def history(self, result, row_id=1):
        return next(item for item in result["items"] if item["table"] == "urls" and item["row_id"] == row_id)

    def test_readonly_row_counts_default_profile_scope_and_no_private_diagnostics(self):
        path = self.create()
        extra = path.parent.parent / "Profile 1"
        extra.mkdir()
        (extra / "History").write_bytes(b"not a supported source")
        before = hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns
        result = browser.scan(roots={"chrome": path.parent})
        self.assertEqual(len(result["items"]), 3)  # 2 urls rows, not 21 visits/count.
        self.assertEqual([d["count"] for d in result["diagnostics"]], [2, 1])
        self.assertNotIn("taobao", repr(result["diagnostics"]))
        self.assertNotIn("secret title", repr(result))
        self.assertEqual(before, (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns))
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_missing_unsupported_and_corrupt_sources_report_distinct_status(self):
        missing = browser.scan(roots={"chrome": self.root / "missing"})
        self.assertEqual({d["status"] for d in missing["diagnostics"]}, {"missing"})
        path = self.create()
        with self.database(path) as connection:
            connection.execute("DROP TABLE downloads")
        result = self.scan(path)
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(any(d["status"] == "unsupported" for d in result["diagnostics"]))
        path.write_bytes(b"corrupt fixture")
        self.assertEqual(self.scan(path)["diagnostics"][0]["status"], "error")

    def test_shopping_full_url_dedup_and_sensitive_case_sensitive_basename_dedup(self):
        chrome, edge = self.create(), self.create("edge")
        document = self.root / "private.pdf"
        document.write_bytes(b"private fixture")
        for path in (chrome, edge):
            with self.database(path) as connection:
                connection.execute("INSERT INTO downloads VALUES(2,?,?,X'01')", (str(document), str(document)))
                connection.execute("INSERT INTO downloads VALUES(3,'missing.PDF','missing.PDF',X'01')")
                connection.execute("INSERT INTO downloads VALUES(4,'other/private.pdf','other/private.pdf',X'01')")
                connection.execute("INSERT INTO urls VALUES(3,'https://www.taobao.com/item/second','',1,113)")
                connection.execute("INSERT INTO urls VALUES(4,'HTTPS://SHOP.JD.COM/item','',1,114)")
        result = browser.scan(roots={"chrome": chrome, "edge": edge})
        self.assertEqual(len(result["shopping_groups"]), 3)
        self.assertEqual([len(group["members"]) for group in result["shopping_groups"]], [2, 2, 2])
        self.assertEqual(len(result["sensitive_items"]), 6)
        self.assertEqual({group["name"] for group in result["sensitive_groups"]}, {"not-on-disk.pdf", "private.pdf"})
        self.assertEqual([len(group["members"]) for group in result["sensitive_groups"]], [2, 4])
        self.assertTrue(document.exists())

    def test_clean_selected_history_relations_keep_unselected_and_bookmarks(self):
        path = self.create(relations=True)
        bookmarks = path.parent / "Bookmarks"
        bookmarks.write_bytes(b"preserved bookmarks")
        result = browser.clean([self.history(self.scan(path))])
        self.assertEqual(len(result["cleaned_ids"]), 1)
        self.assertEqual(result["errors"], [])
        self.assertEqual([r[0] for r in self.rows(path, "urls")], [2])
        self.assertEqual(self.rows(path, "visits"), [(11, 2, 0, 0, 0, 0)])
        for table in ("keyword_search_terms", "visit_source", "content_annotations", "context_annotations",
                      "cluster_visit_duplicates", "segments", "segment_usage", "visited_links", "edge_urls",
                      "edge_visits", "edge_keyword_search_terms_source"):
            self.assertEqual(self.rows(path, table), [], table)
        self.assertEqual(self.rows(path, "clusters"), [(5, "shared")])
        self.assertEqual(self.rows(path, "cluster_keywords"), [(5, "kept")])
        self.assertEqual(len(self.rows(path, "downloads")), 1)
        self.assertEqual(bookmarks.read_bytes(), b"preserved bookmarks")

    def test_clean_download_trace_keeps_downloaded_file_and_history(self):
        path = self.create(relations=True)
        document = self.root / "kept.pdf"
        document.write_bytes(b"kept")
        with self.database(path) as connection:
            connection.execute("UPDATE downloads SET current_path=? WHERE id=1", (str(document),))
        item = next(item for item in self.scan(path)["items"] if item["table"] == "downloads")
        result = browser.clean([item, item])
        self.assertEqual(result["cleaned_ids"], [item["id"]])
        for table in ("downloads", "downloads_url_chains", "downloads_slices", "edge_downloads"):
            self.assertEqual(self.rows(path, table), [])
        self.assertEqual(len(self.rows(path, "urls")), 2)
        self.assertEqual(document.read_bytes(), b"kept")

    def test_changed_or_new_related_record_is_preserved(self):
        path = self.create()
        selected = self.history(self.scan(path))
        with self.database(path) as connection:
            connection.execute("INSERT INTO visits VALUES(12,1,0,0,0,0)")
        result = browser.clean([selected])
        self.assertFalse(result["cleaned_ids"])
        self.assertIn("变化", result["errors"][0]["error"])
        selected = self.history(self.scan(path))
        with self.database(path) as connection:
            connection.execute("UPDATE urls SET title='changed' WHERE id=1")
        self.assertFalse(browser.clean([selected])["cleaned_ids"])
        self.assertEqual(len(self.rows(path, "visits")), 3)

    def test_new_unselected_records_survive_clean_and_prevalidation_precedes_relation_updates(self):
        path = self.create()
        items = [item for item in self.scan(path)["items"] if item["table"] == "urls"]
        with self.database(path) as connection:
            connection.execute("INSERT INTO urls VALUES(3,'https://new.invalid','new',1,113)")
        result = browser.clean(items)
        self.assertEqual(set(result["cleaned_ids"]), {item["id"] for item in items})
        self.assertEqual([row[0] for row in self.rows(path, "urls")], [3])

    def test_forged_and_replaced_database_rows_cannot_be_deleted(self):
        path = self.create()
        selected = self.history(self.scan(path))
        forged = dict(selected, row_id=2)
        self.assertFalse(browser.clean([forged])["cleaned_ids"])
        self.assertFalse(browser.clean([dict(selected, store_token="invented")])["cleaned_ids"])
        replacement = path.with_suffix(".replaced")
        replacement.write_bytes(path.read_bytes())
        path.unlink()
        replacement.rename(path)
        result = browser.clean([selected])
        self.assertFalse(result["cleaned_ids"])
        self.assertIn("替换", result["errors"][0]["error"])
        self.assertEqual(len(self.rows(path, "urls")), 2)

    def test_browser_background_process_refuses_clean_without_database_write(self):
        path = self.create()
        selected = self.history(self.scan(path))
        before = path.read_bytes()
        with patch.object(browser.windows, "running_images", return_value=[Path(r"C:\app\CHROME.EXE")]):
            result = browser.clean([selected])
        self.assertFalse(result["cleaned_ids"])
        self.assertIn("退出", result["errors"][0]["error"])
        self.assertEqual(path.read_bytes(), before)

    def test_schema_changes_unknown_relations_and_triggers_fail_closed(self):
        for alteration in ("CREATE TABLE new_related(visit_id INTEGER)",
                           "CREATE TRIGGER dangerous AFTER DELETE ON urls BEGIN DELETE FROM downloads; END",
                           "ALTER TABLE visits ADD COLUMN unexpected INTEGER"):
            path = self.create(str(hash(alteration)))
            selected = self.history(self.scan(path))
            with self.database(path) as connection:
                connection.execute(alteration)
            self.assertFalse(browser.clean([selected])["cleaned_ids"])
            self.assertEqual(len(self.rows(path, "urls")), 2)
            # An unknown relation/trigger also refuses a fresh scan's authority.
            if "ALTER" not in alteration:
                self.assertFalse(browser.clean([self.history(self.scan(path))])["cleaned_ids"])

    def test_cancellation_rolls_back_entire_database_and_stops_scan(self):
        path = self.create()
        cancel = threading.Event()
        items = self.scan(path)["items"]
        result = browser.clean(items, cancel, lambda event: cancel.set())
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["cleaned_ids"])
        self.assertEqual(len(self.rows(path, "urls")), 2)
        self.assertEqual(len(self.rows(path, "downloads")), 1)
        cancelled = browser.scan(cancel, roots={"chrome": path})
        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(cancelled["items"], [])

    def test_browser_starting_during_cleanup_rolls_back(self):
        path = self.create()
        selected = self.history(self.scan(path))
        with patch.object(browser.windows, "running_images", side_effect=[[], [Path("chrome.exe")]]):
            result = browser.clean([selected])
        self.assertFalse(result["cleaned_ids"])
        self.assertEqual(len(self.rows(path, "urls")), 2)

    def test_wal_backup_includes_committed_records_without_checkpointing_source(self):
        path = self.create()
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("INSERT INTO urls VALUES(3,'https://wal.invalid','wal',1,113)")
            connection.commit()
            before = path.read_bytes(), path.stat().st_mtime_ns
            result = self.scan(path)
            self.assertEqual(len([i for i in result["items"] if i["table"] == "urls"]), 3)
            self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))
        finally:
            connection.close()

    def test_derived_group_members_clean_all_underlying_records_once(self):
        chrome, edge = self.create(), self.create("edge")
        result = browser.scan(roots={"chrome": chrome, "edge": edge})
        group = result["shopping_groups"][0]
        cleaned = browser.clean([{"id": "shopping", "members": group["members"]}, group["members"][0]])
        self.assertEqual(set(cleaned["cleaned_ids"]), {item["id"] for item in group["members"]})
        self.assertEqual(len(self.rows(chrome, "urls")), 1)
        self.assertEqual(len(self.rows(edge, "urls")), 1)


if __name__ == "__main__":
    unittest.main()
