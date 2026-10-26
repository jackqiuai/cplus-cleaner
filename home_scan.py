"""Read-only homepage scan orchestration over the existing feature backends.

This module has no Tk dependency. Returned payloads retain the real snapshots
needed to open each feature's results without repeating an expensive scan.
"""
from __future__ import annotations

import os
import threading

import cdrive
import chat_cleanup
import cdrive_system
import core
import large_files
import popup
import privacy
import software_compression
import software_migration
import software_uninstall


FEATURES = ("cdrive", "chat", "large", "privacy", "compress", "popup", "migrate", "uninstall")
LABELS = dict(cdrive="C盘清理", chat="聊天清理", large="大文件搬家",
              privacy="隐私清理", compress="软件压缩", popup="弹窗拦截", migrate="软件搬家", uninstall="软件卸载")
UNITS = {key: "条" if key == "privacy" else "项" if key == "popup" else "款" if key == "uninstall" else "bytes" for key in FEATURES}


def _path_key(path):
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def _unique_bytes(items):
    # The first observed snapshot owns a shared path, matching CleanupSession.
    seen, total = set(), 0
    for item in items:
        key = _path_key(item.path)
        if key not in seen:
            seen.add(key)
            total += item.size
    return total


def _cleanup(key, cancel, report, folders):
    diagnostics = []
    options = {name for name, selected in cdrive.SCAN_OPTIONS if selected}
    rules = [rule for rule in cdrive.build_rules(days=0)
             if cdrive.scan_option(rule.group, rule.name) in options]
    payload = dict(rules=rules, items=[], system_items=cdrive_system.read_system_items(),
                   manifest_items=None, diagnostics=diagnostics)
    def batch(items):
        payload["items"].extend(items)
        report(dict(message=f"已发现 {len(payload['items'])} 个文件", value=_unique_bytes(payload["items"])))
    payload["items"] = core.scan_rules(rules, cancel, report, diagnostics=diagnostics, on_batch=batch)
    errors = sum(item.get("errors", 0) for item in diagnostics)
    return payload, f"扫描完成，{errors} 项无法读取" if errors else "扫描完成"


def cleanup_bytes(results):
    """Unique bytes in file-cleanup snapshots, excluding migration/compression."""
    return _unique_bytes(item for key in ("cdrive", "chat")
                         for item in ((results.get(key) or {}).get("payload") or {}).get("items", []))


def scan(cancel=None, progress=None, *, folders=None, enabled=None):
    """Scan selected features serially, retaining failures and cancellation.

    ``progress`` receives key/state/completed/total/message dictionaries. Unknown
    work totals stay None; a terminal done update alone reports 1/1. A failed or
    cancelled stage retains its last actual progress instead of claiming 100%.
    """
    cancel = cancel if cancel is not None else threading.Event()
    folders = dict(folders or {})
    selected = set(FEATURES if enabled is None else enabled)
    unknown = selected.difference(FEATURES)
    if unknown:
        raise ValueError("未知首页功能：" + "、".join(sorted(unknown)))
    results = {}
    for key in FEATURES:
        if key in selected and progress:
            progress(dict(key=key, state="waiting", completed=None, total=None, message="等待扫描"))
    for key in FEATURES:
        if key not in selected or cancel.is_set():
            continue
        last = dict(completed=None, total=None)

        def report(update):
            data = dict(update) if isinstance(update, dict) else dict(message=str(update))
            # Missing totals must not inherit a previous stage's denominator.
            completed, total = data.get("completed"), data.get("total")
            if not isinstance(total, (int, float)) or total <= 0:
                completed = total = None
            elif not isinstance(completed, (int, float)):
                completed = None
            else:
                completed = max(0, min(completed, total))
            last.update(completed=completed, total=total)
            if progress:
                progress(dict(data, key=key, state="running", completed=completed, total=total,
                              message=str(data.get("message", "正在扫描…")), unit=UNITS[key]))

        result = dict(key=key, value=0, unit=UNITS[key], payload=None,
                      cancelled=False, error="", diagnostics=[], message="扫描完成")
        report(dict(message=f"正在扫描{LABELS[key]}…"))
        try:
            if key == "cdrive":
                payload, message = _cleanup(key, cancel, report, folders)
                result.update(payload=payload, value=_unique_bytes(payload["items"]),
                              diagnostics=payload["diagnostics"], message=message)
            elif key == "chat":
                payload = chat_cleanup.scan(cancel, report)
                result.update(payload=payload, value=_unique_bytes(payload["items"]),
                              diagnostics=payload["errors"], cancelled=payload["cancelled"])
            elif key == "privacy":
                payload = privacy.scan(cancel, report)
                result.update(payload=payload, value=payload.total_count, diagnostics=list(payload.problems),
                              cancelled=payload.cancelled)
            else:
                if key == "large":
                    payload = large_files.scan(cancel, report)
                elif key == "compress":
                    payload = software_compression.scan(cancel, report)
                elif key == "popup":
                    payload = popup.scan(cancel, stage_progress=report)
                elif key == "uninstall":
                    payload = software_uninstall.scan(cancel, report)
                else:
                    payload = software_migration.scan(cancel, on_progress=report)
                value = (len(payload["items"]) if key == "uninstall" else
                         sum(len(item.rules) for item in payload["items"]) if key == "popup" else
                         sum(item.size for item in payload["items"]
                             if key not in ("compress", "migrate") or not item.blocked))
                result.update(payload=payload, value=value, diagnostics=list(payload.get("errors") or []),
                              cancelled=bool(payload.get("cancelled")))
            result["cancelled"] = bool(result["cancelled"] or cancel.is_set())
        except Exception as error:
            # One inaccessible software source must not suppress unrelated scans.
            result.update(error=str(error), message=f"扫描未完成：{error}", cancelled=cancel.is_set())
        results[key] = result
        state = "cancelled" if result["cancelled"] else "failed" if result["error"] else "done"
        if state == "done":
            last.update(completed=1, total=1)
        if state == "cancelled":
            result["message"] = "扫描已取消，保留已找到的结果"
        if progress:
            progress(dict(key=key, state=state, value=result["value"], unit=result["unit"],
                          message=result["message"], **last))
    return dict(results=results, cancelled=cancel.is_set(), cleanup_bytes=cleanup_bytes(results))
