"""Adopt read-only homepage snapshots on the Tk thread without rescanning.

The feature backends keep their normal authorization checks for later actions.
Completed popup results also enable supported window-only blocking, following
the popup page's scan defaults. Other results only update their existing views.
"""
from __future__ import annotations


FEATURES = frozenset(("cdrive", "chat", "large", "privacy", "compress", "popup", "migrate", "uninstall"))


def _message(feature):
    message = str(feature.get("message") or "首页扫描完成")
    if feature.get("cancelled") and "取消" not in message:
        message = "扫描已取消，保留已找到的结果。" + message
    return message


def _cancel_open(page):
    pending = getattr(page, "_pending_open", None)
    if pending is not None:
        page.after_cancel(pending)
        page._pending_open = None


def _disk_summary(page):
    from cplus_cleaner.ui.desktop import disk_name, disk_snapshot, size_text
    try:
        info = disk_snapshot()
        page.disk.set(f"{disk_name(info)}共 {size_text(info['total'])}，可用 {size_text(info['free'])}")
    except OSError:
        page.disk.set(f"{disk_name()}空间暂时无法读取")


def _cleanup(page, key, payload, feature):
    from cplus_cleaner.features.drive_cleanup import cdrive

    page.scanning = False
    if page._paint_pending is not None:
        page.after_cancel(page._paint_pending)
        page._paint_pending = None
    page.days.set("0")
    page.range_label.set("全部时间")
    if key == "cdrive":
        for name, checked in cdrive.SCAN_OPTIONS:
            page.scan_options[name].set(checked)
    elif payload.get("folder"):
        page.folder.set(payload["folder"])
    page.scan_details = list(payload.get("diagnostics", []))
    page.prepare((payload["rules"], payload.get("system_items", []), payload.get("manifest_items")))
    page.consume(payload["items"])
    if key == "cdrive":
        page.apply_defaults()
    page.flush_paint()
    page.note.set(_message(feature))


def _privacy(page, payload, feature):
    page.session = payload
    page.clean_errors = []
    page.build_groups()
    page.update_totals()
    page.status.set(_message(feature) +
                    (f" · {len(payload.problems)}项资料无法完整读取，已跳过" if payload.problems else ""))


def _large(page, payload, feature):
    from cplus_cleaner.features.large_files import large_files

    page.scanning = False
    page.scan_details = payload
    page.items = list(payload["items"])
    page.blocked_paths = dict(payload.get("blocked_paths", {}))
    page.selected = {item.path for item in page.items if item.path not in page.blocked_paths}
    page.scanned = True
    page.idle.pack_forget()
    page.results.pack(fill="both", expand=True)
    page.render()
    page.scan_button.caption = "扫描"
    page.scan_button.set_enabled(True)
    page.scan_button.pack_forget()
    page.move_button.pack(side="right", before=page.title_frame)
    if feature.get("cancelled"):
        page.progress_value.set(0)
    page.finish_progress(_message(feature), not feature.get("cancelled"))
    page.note.set(f"全部时间 · {large_files.threshold_description(payload['minimum'])}；" +
                  _message(feature) +
                  (f" {len(page.blocked_paths)}项受保护或运行中，仅展示。" if page.blocked_paths else "") +
                  (f" {payload['counts'].get('errors', 0)}项无法读取或已跳过。"
                   if payload.get("counts", {}).get("errors") else ""))
    page.refresh_disks()


def _migration(page, payload, feature):
    page.scanning = False
    page.scan_summary = payload
    page.selected.clear()
    page.items = list(payload["items"])
    page.icons.clear()
    page.render()
    if feature.get("cancelled"):
        page.progress_value.set(0)
    page.finish_page_progress(_message(feature), not feature.get("cancelled"))
    skipped = sum(payload.get("skipped", {}).values())
    page.note.set(_message(feature) +
                  (f" 已排除{payload['excluded_services']}个含系统服务的软件。" if payload.get("excluded_services") else "") +
                  (f" {skipped}个目录无法完整读取或含链接，已跳过。" if skipped else ""))
    _disk_summary(page)


def _compression(page, payload, feature):
    page.scanning = False
    page._loaded = True
    page.progress_frame.pack_forget()
    page.selected.clear()
    page.results, page.errors = [], {}
    page.icons.clear()
    page.apps = [item for item in payload["items"] if not item.blocked]
    page.states = {item.key: "ready" for item in page.apps}
    page.search.set("")
    page.render()
    errors = payload.get("errors", [])
    page.note.set(_message(feature) + (f" {len(errors)}项软件资料无法读取。" if errors else ""))


def _popup(page, payload, feature):
    page.scanning = False
    page._loaded = True
    from cplus_cleaner.features.popup.popup import visible_items
    page.items = visible_items(payload["items"])
    page.blocker.set_items(page.items)
    setting_error = ''
    if not feature.get('cancelled') and not payload.get('cancelled'):
        try:
            page.blocker.enable_detected()
        except (OSError, ValueError) as error:
            setting_error = f' 自动开启设置未保存：{error}'
    page.render()
    page._scan_fraction = 0
    page.finish_scan_progress(_message(feature), complete=not feature.get("cancelled"))
    errors = payload.get("errors", [])
    page.note.set(_message(feature) +
                  (f" {len(errors)}项软件信息或图标无法读取，已跳过。" if errors else "") +
                  setting_error +
                  (" " + page.blocker.error if page.blocker.error else ""))
    page._reload_history()


def _uninstall(page, payload, feature):
    from cplus_cleaner.features.uninstall.software_uninstall_ui import error_text
    page.scanning = False
    page._loaded = True
    page.selected.clear()
    page.results, page.queue, page.errors = [], [], {}
    page.icons.clear()
    page.apps = list(payload['items'])
    page.states = {item.key: 'ready' for item in page.apps}
    page.search.set('')
    page.render()
    _disk_summary(page)
    errors = error_text(payload.get('errors'))
    page.note.set(_message(feature) + (' ' + errors if errors else ''))


def apply_result(app, key, feature):
    """Return True when a homepage snapshot was adopted without rescanning.

    The caller navigates with ``use_existing=True`` so an on-open auto scan does
    not replace this result. A failed or missing snapshot uses the normal page.
    """
    if (getattr(app, "busy", False) or key not in FEATURES or not isinstance(feature, dict)
            or feature.get("error") or feature.get("payload") is None):
        return False
    payload = feature["payload"]
    if key == "privacy":
        if not hasattr(payload, "categories"):
            return False
    elif key == "chat":
        if not isinstance(payload, dict) or not all(field in payload for field in ("rules", "items", "manifest_items", "apps")):
            return False
    elif not isinstance(payload, dict) or not all(field in payload for field in (
            ("rules", "items") if key in ("cdrive", "chat") else ("items",))):
        return False
    page = app.ensure_view(key)
    if any(bool(getattr(page, flag, False)) for flag in ("scanning", "processing", "queue_active", "_destroyed")):
        return False
    _cancel_open(page)
    if key == "chat":
        page.adopt(payload, _message(feature))
    elif key == "cdrive":
        _cleanup(page, key, payload, feature)
    else:
        {"privacy": _privacy, "large": _large, "migrate": _migration,
         "compress": _compression, "popup": _popup, "uninstall": _uninstall}[key](page, payload, feature)
    return True


def refresh_cleanup(app):
    """Reconcile overlapping C-drive/chat snapshots after a completed deletion."""
    import os
    from cplus_cleaner.features.home.home_scan import _unique_bytes
    for key in ("cdrive", "chat"):
        page = app.views.get(key)
        if page is not None and getattr(page, "session", None) is not None:
            page.session.refresh_after_delete()
            page.update_totals()
        feature = app.home_page.results.get(key)
        if feature and isinstance(feature.get('payload'), dict):
            payload = feature['payload']
            remaining = []
            for item in payload.get('items', []):
                try:
                    os.lstat(item.path)
                except FileNotFoundError:
                    continue
                except OSError:
                    pass
                remaining.append(item)
            payload['items'] = remaining
            feature['value'] = _unique_bytes(remaining)
            app.set_nav_metric(key, feature['value'])
