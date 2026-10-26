"""Chromium privacy records, using the original cleaner's Default-profile scope.

Scanning reads an SQLite backup in memory, never titles or URLs into diagnostics.
Cleaning is an independent, conservative implementation: it accepts only records
issued by this process, checks their database/row identity and relationships, and
requires the corresponding browser to be closed. Downloaded files are not touched.
"""
from __future__ import annotations

import base64
import hashlib
import json
import ntpath
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import threading
import time

import software_windows as windows


_BROWSERS = {"chrome": ("Google/Chrome/User Data/Default", "chrome.exe"),
             "edge": ("Microsoft/Edge/User Data/Default", "msedge.exe")}
SHOPPING_DOMAINS = ("taobao.com", "jd.com", "tmall.com", "amazon.com", "dangdang.com",
                    "suning.com", "vip.com", "pinduoduo.com")
SENSITIVE_SUFFIXES = (".doc", ".docx", ".pdf", ".txt", ".xls", ".xlsx", ".ppt", ".pptx",
                      ".odt", ".ods", ".odp")
_REQUIRED = {"urls": {"id", "url"}, "downloads": {"id", "current_path"}}
_RELATIONS = {
    "visits": {"id", "url"}, "visit_source": {"id"},
    "keyword_search_terms": {"url_id"}, "content_annotations": {"visit_id"},
    "context_annotations": {"visit_id"}, "clusters_and_visits": {"cluster_id", "visit_id"},
    "cluster_visit_duplicates": {"visit_id", "duplicate_visit_id"},
    "clusters": {"cluster_id"}, "cluster_keywords": {"cluster_id"},
    "segments": {"id", "url_id"}, "segment_usage": {"segment_id"},
    "visited_links": {"id", "link_url_id"}, "edge_urls": {"url_id"},
    "edge_keyword_search_terms_source": {"url_id"}, "edge_visits": {"visit_id"},
    "downloads_url_chains": {"id"}, "downloads_slices": {"download_id"},
    "edge_downloads": {"id"},
}
_TOKENS = {}
_LOCK = threading.RLock()


class Cancelled(Exception):
    pass


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def default_roots():
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    return {browser: local / relative for browser, (relative, _) in _BROWSERS.items()}


def _roots(roots):
    result = {}
    for browser, root in (default_roots() if roots is None else roots).items():
        if browser not in _BROWSERS:
            raise ValueError("不支持的浏览器")
        path = Path(os.path.abspath(root))
        result[browser] = path if path.name == "History" else path / "History"
    return result


def _file_identity(path):
    for parent in (path, *path.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("浏览器资料路径包含链接或重解析目录")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
        raise ValueError("浏览器资料不是独立普通文件")
    return info.st_dev, info.st_ino


def _value(value):
    return {"bytes": base64.b64encode(value).decode("ascii")} if isinstance(value, bytes) else value


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":"),
                                     sort_keys=True).encode("utf-8")).hexdigest()


def _row_fingerprint(columns, row):
    return _digest([list(columns), [_value(value) for value in row]])


def _schema(connection, *, writing=False):
    rows = connection.execute("SELECT name, type, sql FROM sqlite_master ORDER BY name").fetchall()
    tables = {}
    for name, kind, sql in rows:
        if kind == "table" and sql and not sql.lstrip().upper().startswith("CREATE VIRTUAL"):
            tables[name] = tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{name.replace(chr(34), chr(34)*2)}")'))
    if writing:
        if any(kind == "trigger" for _, kind, _ in rows):
            raise ValueError("资料库含未知触发器，已保留记录")
        for name, columns in {**_REQUIRED, **_RELATIONS}.items():
            if name in tables and not columns.issubset(tables[name]):
                raise ValueError(f"不支持的关联表结构：{name}")
        # Unknown ID-bearing relations cannot safely be left dangling.
        known = set(_REQUIRED) | set(_RELATIONS)
        for name, columns in tables.items():
            if name not in known and {"url_id", "visit_id", "download_id", "link_url_id"}.intersection(columns):
                raise ValueError(f"未知关联表：{name}")
    return tables, _digest(rows)


def _fetch(connection, schema, table, column, values):
    if table not in schema:
        return []
    if column not in schema[table]:
        raise ValueError(f"不支持的关联表结构：{table}")
    result = []
    for value in set(values):
        result.extend(connection.execute(f'SELECT * FROM "{table}" WHERE "{column}"=?', (value,)).fetchall())
    # Relations with more than one matching column may contain duplicate rows.
    return result


def _related(connection, schema, table, row_id):
    """Exact associated rows establish whether a selected trace changed."""
    data = {}
    def collect(name, column, values):
        rows = _fetch(connection, schema, name, column, values)
        if name in schema:
            data.setdefault(name, []).extend([[_value(v) for v in row] for row in rows])
        return rows
    if table == "downloads":
        collect("downloads_url_chains", "id", [row_id])
        collect("downloads_slices", "download_id", [row_id])
        collect("edge_downloads", "id", [row_id])
    else:
        visits = collect("visits", "url", [row_id])
        visit_ids = [row[schema["visits"].index("id")] for row in visits]
        collect("visit_source", "id", visit_ids)
        for name in ("content_annotations", "context_annotations", "clusters_and_visits", "edge_visits"):
            collect(name, "visit_id", visit_ids)
        collect("cluster_visit_duplicates", "visit_id", visit_ids)
        collect("cluster_visit_duplicates", "duplicate_visit_id", visit_ids)
        for name in ("keyword_search_terms", "edge_urls", "edge_keyword_search_terms_source"):
            collect(name, "url_id", [row_id])
        collect("visited_links", "link_url_id", [row_id])
        segments = collect("segments", "url_id", [row_id])
        segment_ids = [row[schema["segments"].index("id")] for row in segments]
        collect("segment_usage", "segment_id", segment_ids)
    return _digest({name: sorted(rows, key=repr) for name, rows in data.items()})


def _snapshot(path, cancel):
    _check_cancel(cancel)
    _file_identity(path)
    # A read-only WAL connection needs an existing shared-memory sidecar. Do not
    # create one beside a real browser profile while trying to inspect it.
    if Path(str(path) + "-wal").exists() and not Path(str(path) + "-shm").exists():
        raise ValueError("WAL快照缺少共享内存文件，请先退出浏览器后重试")
    source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    memory = sqlite3.connect(":memory:")
    deadline = time.monotonic() + 10
    def step(status, remaining, total):
        _check_cancel(cancel)
        if time.monotonic() > deadline:
            raise TimeoutError("浏览器资料繁忙，请稍后重试")
    try:
        source.execute("PRAGMA query_only=ON")
        source.execute("PRAGMA trusted_schema=OFF")
        source.backup(memory, pages=128, progress=step, sleep=.01)
        memory.execute("PRAGMA query_only=ON")
        memory.execute("PRAGMA trusted_schema=OFF")
        return memory
    except BaseException:
        memory.close()
        raise
    finally:
        source.close()


def scan(cancel=None, progress=None, *, roots=None, shopping_domains=SHOPPING_DOMAINS,
         sensitive_suffixes=SENSITIVE_SUFFIXES):
    """Return one item per urls/downloads row; Default only, no age restriction.

    ``roots`` maps chrome/edge to a Default folder (or its History file). Derived
    matching uses literal recovered patterns supplied by the caller. Raw URL and
    current_path metadata must remain in memory; diagnostics contain counts only.
    """
    items, diagnostics, stores = [], [], {}
    cancelled = False
    try:
        for browser, path in _roots(roots).items():
            _check_cancel(cancel)
            if progress:
                progress({"stage": "browser", "message": f"读取 {browser} 隐私记录", "completed": None, "total": None})
            if not path.exists():
                diagnostics.extend({"category": f"{browser}_{kind}", "source": str(path),
                                    "status": "missing", "error": "资料库不存在", "count": 0}
                                   for kind in ("history", "downloads"))
                continue
            connection = None
            try:
                identity = _file_identity(path)
                connection = _snapshot(path, cancel)
                schema, schema_fingerprint = _schema(connection)
                token = secrets.token_hex(24)
                store = {"browser": browser, "path": str(path), "identity": identity,
                         "schema": schema_fingerprint, "items": {}}
                browser_items = []
                for table, kind in (("urls", "history"), ("downloads", "downloads")):
                    category = f"{browser}_{kind}"
                    if table not in schema or not _REQUIRED[table].issubset(schema[table]):
                        diagnostics.append({"category": category, "source": str(path), "status": "unsupported",
                                            "error": f"不支持的表结构：{table}", "count": 0})
                        continue
                    count = 0
                    for row in connection.execute(f'SELECT * FROM "{table}"'):
                        _check_cancel(cancel)
                        record = dict(zip(schema[table], row))
                        if not isinstance(record["id"], int):
                            raise ValueError("记录ID格式不支持")
                        row_id = record["id"]
                        item_id = f"{browser}:{_digest(str(path))[:16]}:{table}:{row_id}"
                        item = {"id": item_id, "category": category, "kind": "browser", "browser": browser,
                                "source": str(path), "table": table, "row_id": row_id,
                                "fingerprint": _row_fingerprint(schema[table], row),
                                "relation_fingerprint": _related(connection, schema, table, row_id),
                                "store_token": token}
                        item["url" if table == "urls" else "current_path"] = str(record.get("url" if table == "urls" else "current_path") or "")
                        store["items"][item_id] = dict(item)
                        browser_items.append(item)
                        count += 1
                    diagnostics.append({"category": category, "source": str(path), "status": "ok", "error": "", "count": count})
                stores[token] = store
                items.extend(browser_items)
            except Cancelled:
                raise
            except (sqlite3.Error, OSError, ValueError, TimeoutError) as error:
                # sqlite diagnostics may embed schema/SQL, but never dump rows.
                diagnostics[:] = [d for d in diagnostics if d.get('source') != str(path)]
                diagnostics.extend({"category": f"{browser}_{kind}", "source": str(path),
                                    "status": "error", "error": str(error), "count": 0}
                                   for kind in ('history', 'downloads'))
            finally:
                if connection is not None:
                    connection.close()
    except Cancelled:
        cancelled = True
    with _LOCK:
        _TOKENS.update(stores)
        while len(_TOKENS) > 32:
            _TOKENS.pop(next(iter(_TOKENS)))
    shopping = {}
    sensitive = {}
    for item in items:
        if item["table"] == "urls" and any(domain.lower() in item["url"].lower() for domain in shopping_domains):
            shopping.setdefault(item["url"], []).append(item)
        if item["table"] == "downloads" and any(item["current_path"].endswith(suffix) for suffix in sensitive_suffixes):
            sensitive.setdefault(ntpath.basename(item["current_path"]), []).append(item)
    return {"items": items, "diagnostics": diagnostics, "cancelled": cancelled,
            "shopping_groups": [{"url": url, "members": members} for url, members in shopping.items()],
            "sensitive_groups": [{"name": name, "members": members} for name, members in sensitive.items()],
            "sensitive_items": [item for members in sensitive.values() for item in members]}


def _browser_running(browser):
    expected = _BROWSERS[browser][1]
    return any(Path(image).name.casefold() == expected for image in windows.running_images())


def _delete_where(connection, schema, table, column, values):
    if table in schema:
        for value in set(values):
            connection.execute(f'DELETE FROM "{table}" WHERE "{column}"=?', (value,))


def _delete_record(connection, schema, table, row_id):
    delete = lambda name, column, values: _delete_where(connection, schema, name, column, values)
    if table == "downloads":
        delete("downloads_url_chains", "id", [row_id])
        delete("downloads_slices", "download_id", [row_id])
        delete("edge_downloads", "id", [row_id])
    else:
        visits = _fetch(connection, schema, "visits", "url", [row_id])
        visit_ids = [row[schema["visits"].index("id")] for row in visits]
        clusters = _fetch(connection, schema, "clusters_and_visits", "visit_id", visit_ids)
        cluster_ids = [row[schema["clusters_and_visits"].index("cluster_id")] for row in clusters]
        delete("visit_source", "id", visit_ids)
        for name in ("content_annotations", "context_annotations", "clusters_and_visits", "edge_visits"):
            delete(name, "visit_id", visit_ids)
        delete("cluster_visit_duplicates", "visit_id", visit_ids)
        delete("cluster_visit_duplicates", "duplicate_visit_id", visit_ids)
        # Retained visits must not refer to the removed visit IDs.
        if "visits" in schema:
            for column in ("from_visit", "opener_visit"):
                if column in schema["visits"]:
                    for visit_id in visit_ids:
                        connection.execute(f'UPDATE visits SET "{column}"=0 WHERE "{column}"=?', (visit_id,))
        delete("visits", "id", visit_ids)
        for cluster_id in set(cluster_ids):
            if not connection.execute("SELECT 1 FROM clusters_and_visits WHERE cluster_id=? LIMIT 1", (cluster_id,)).fetchone():
                delete("cluster_keywords", "cluster_id", [cluster_id])
                delete("clusters", "cluster_id", [cluster_id])
        for name in ("keyword_search_terms", "edge_urls", "edge_keyword_search_terms_source"):
            delete(name, "url_id", [row_id])
        links = _fetch(connection, schema, "visited_links", "link_url_id", [row_id])
        link_ids = [row[schema["visited_links"].index("id")] for row in links]
        if "visits" in schema and "visited_link_id" in schema["visits"]:
            for link_id in link_ids:
                connection.execute("UPDATE visits SET visited_link_id=0 WHERE visited_link_id=?", (link_id,))
        delete("visited_links", "link_url_id", [row_id])
        segments = _fetch(connection, schema, "segments", "url_id", [row_id])
        segment_ids = [row[schema["segments"].index("id")] for row in segments]
        delete("segment_usage", "segment_id", segment_ids)
        if "visits" in schema and "segment_id" in schema["visits"]:
            for segment_id in segment_ids:
                connection.execute("UPDATE visits SET segment_id=0 WHERE segment_id=?", (segment_id,))
        delete("segments", "url_id", [row_id])
    delete(table, "id", [row_id])


def clean(items, cancel=None, progress=None):
    """Delete only selected, unchanged scanned rows; all changes are per-DB atomic.

    Derived items can supply ``members`` containing their original browser items.
    ``cleaned_ids`` always contains the physical record IDs, allowing callers to
    reconcile overlapping history and shopping categories without double counting.
    """
    groups, errors, cleaned = {}, [], []
    def error(item, message):
        errors.append({"id": item.get("id", ""), "category": item.get("category", ""), "error": message})
    flattened = []
    for item in items:
        flattened.extend(item.get("members", [item]))
    with _LOCK:
        for item in flattened:
            store = _TOKENS.get(item.get("store_token", ""))
            original = store["items"].get(item.get("id")) if store else None
            if original is None or any(item.get(key) != original[key] for key in
                    ("source", "table", "row_id", "fingerprint", "relation_fingerprint", "browser")):
                error(item, "不是本次扫描授权的记录，请重新扫描")
                continue
            groups.setdefault(item["store_token"], {"store": store, "items": {}})["items"][item["id"]] = original
    cancelled = False
    for group in groups.values():
        store, records = group["store"], list(group["items"].values())
        connection = None
        try:
            _check_cancel(cancel)
            if _browser_running(store["browser"]):
                raise ValueError("请先退出对应浏览器（包括后台进程）再清理")
            path = Path(store["path"])
            if _file_identity(path) != store["identity"]:
                raise ValueError("资料库已替换，请重新扫描")
            connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=2)
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            schema, fingerprint = _schema(connection, writing=True)
            if fingerprint != store["schema"]:
                raise ValueError("资料库表结构已变化，请重新扫描")
            valid = []
            for item in records:
                _check_cancel(cancel)
                table = item["table"]
                row = connection.execute(f'SELECT * FROM "{table}" WHERE id=?', (item["row_id"],)).fetchone()
                if row is None or _row_fingerprint(schema[table], row) != item["fingerprint"] or _related(connection, schema, table, item["row_id"]) != item["relation_fingerprint"]:
                    error(item, "记录或关联记录已变化，已保留；请重新扫描")
                    continue
                valid.append(item)
            for index, item in enumerate(valid):
                _check_cancel(cancel)
                _delete_record(connection, schema, item["table"], item["row_id"])
                if progress:
                    progress({"stage": "browser_clean", "message": "清理所选浏览器记录", "completed": index + 1, "total": len(valid)})
            _check_cancel(cancel)
            if _browser_running(store["browser"]) or _file_identity(path) != store["identity"]:
                raise ValueError("浏览器已启动或资料库被替换，已回滚")
            connection.commit()
            cleaned.extend(item["id"] for item in valid)
        except Cancelled:
            cancelled = True
            break
        except (sqlite3.Error, OSError, ValueError) as failure:
            for item in records:
                error(item, str(failure))
        finally:
            if connection is not None:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()
    return {"cleaned_ids": list(dict.fromkeys(cleaned)), "errors": errors, "cancelled": cancelled}
