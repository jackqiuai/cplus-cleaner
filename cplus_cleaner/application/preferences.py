"""Persisted desktop preferences and an opt-in resident daily cleanup timer."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from cplus_cleaner.common import core

VERSION = "1.1.0"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "CCCLEANER"
DEFAULTS = dict(autostart=False, scheduled_cleanup=False, cleanup_time="20:00",
                close_to_tray=False, last_cleanup_date="", language="")


def validate(values):
    result = {key: values.get(key, default) for key, default in DEFAULTS.items()}
    if result['language'] not in ('', 'zh', 'en'):
        raise ValueError('语言选项无效')
    for key in ("autostart", "scheduled_cleanup", "close_to_tray"):
        if not isinstance(result[key], bool):
            raise ValueError("设置开关必须为布尔值")
    if not isinstance(result["cleanup_time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", result["cleanup_time"]):
        raise ValueError("请输入有效的每日时间，例如 20:00（24小时制）")
    date = result["last_cleanup_date"]
    if not isinstance(date, str):
        raise ValueError("上次清理日期无效")
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except (TypeError, ValueError):
            raise ValueError("上次清理日期无效") from None
    return result


def load():
    try:
        data = json.loads((core.DATA_DIR / "settings.json").read_text(encoding="utf-8"))
        return validate(data)
    except (OSError, ValueError, TypeError, AttributeError):
        return dict(DEFAULTS)


def save(values):
    result = validate(values)
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = core.DATA_DIR / "settings.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return result


def startup_command():
    executable = Path(sys.executable)
    windowed = executable.with_name("pythonw.exe")
    if windowed.exists():
        executable = windowed
    return subprocess.list2cmdline([str(executable), str(core.APP_DIR / "app.py"), "--background"])


def read_autostart():
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_NAME)[0] == startup_command()
    except FileNotFoundError:
        return False


def set_autostart(enabled):
    if os.name != "nt":
        if enabled:
            raise OSError("开机自启动仅支持 Windows")
        return
    import winreg
    command = startup_command()
    if enabled and len(command) > 260:
        raise ValueError("启动路径过长，无法注册开机自启动")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


class DailyScheduler:
    """Run only while this desktop process is alive; never start overdue on enable."""
    def __init__(self, values, now=None):
        self.configure(values, now)

    def configure(self, values, now=None):
        self.values = validate(values)
        now = now or datetime.now()
        self.next_run = None
        if self.values["scheduled_cleanup"]:
            hour, minute = map(int, self.values["cleanup_time"].split(":"))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            last = self.values['last_cleanup_date']
            if last and target.date().isoformat() <= last:
                completed = datetime.strptime(last, '%Y-%m-%d')
                try:
                    target = target.replace(year=completed.year, month=completed.month, day=completed.day) + timedelta(days=1)
                except OverflowError:
                    return  # No representable day follows a completed 9999-12-31.
            self.next_run = target

    def due(self, now=None, busy=False):
        now = now or datetime.now()
        return (not busy and self.next_run is not None and now >= self.next_run
                and now.date().isoformat() > self.values['last_cleanup_date'])

    def mark_started(self, now=None):
        now = now or datetime.now()
        self.values["last_cleanup_date"] = now.date().isoformat()
        self.configure(self.values, now)


def scheduled_cleanup(cancel, progress=None):
    """Fresh all-time scan; select only the reviewed ordinary defaults."""
    from cplus_cleaner.features.drive_cleanup import cdrive
    from cplus_cleaner.features.drive_cleanup.cleanup_model import CleanupSession
    rules = [rule for rule in cdrive.build_rules(days=0)
             if cdrive.scan_option(rule.group, rule.name) in {name for name, selected in cdrive.SCAN_OPTIONS if selected}]
    session = CleanupSession(rules)
    items = core.scan_rules(rules, cancel, progress)
    if cancel.is_set():
        return dict(deleted=0, bytes=0, errors=[], cancelled=True)
    session.consume(items)
    defaults = cdrive.default_selected_categories()
    for category in session.categories:
        if f"{category.group} / {category.name}" in defaults:
            session.set_selected(category.key, True)
    return core.delete_files(session.selected_items(), cancel, progress)


def append_activity(action, **fields):
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = dict(time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), action=action, **fields)
    with (core.DATA_DIR / "activity.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


OPERATION_LOGS = ("operations.jsonl", "system-actions.jsonl", "activity.jsonl")


class OperationRecord(dict):
    """An audit row with a private, exact source-line identity for deletion."""
    def __init__(self, values, filename, line_number, line):
        super().__init__(values)
        self._origin = (filename, line_number, hashlib.sha256(line).digest())


def read_operations(limit=500):
    records, issues = [], []
    for filename in OPERATION_LOGS:
        try:
            with (core.DATA_DIR / filename).open("rb") as stream:
                lines = deque(enumerate(stream), maxlen=limit)
            for line_number, line in lines:
                try:
                    record = json.loads(line.decode("utf-8", errors="replace"))
                    if isinstance(record, dict):
                        records.append(OperationRecord(record, filename, line_number, line))
                except ValueError:
                    issues.append(f"{filename} 中存在不完整记录，已跳过")
        except FileNotFoundError:
            pass
        except OSError as exc:
            issues.append(f"{filename}：{exc}")
    records.sort(key=lambda record: str(record.get("time", "")), reverse=True)
    return records[:limit], list(dict.fromkeys(issues))


def delete_operations(records):
    """Remove only selected audit lines; keep recovery manifests and other rows."""
    selected = {}
    for record in records:
        if not isinstance(record, OperationRecord):
            raise ValueError("记录已变化，请重新打开操作记录后重试")
        filename, line_number, digest = record._origin
        if filename not in OPERATION_LOGS or not isinstance(line_number, int) or line_number < 0:
            raise ValueError("无效的操作记录来源")
        selected.setdefault(filename, {})[line_number] = digest
    return _remove_operations(selected)


def clear_operations():
    """Clear all audit streams, including rows outside the display limit."""
    return _remove_operations(dict.fromkeys(OPERATION_LOGS))


def _remove_operations(selected):
    deleted, errors = 0, []
    for filename, rows in selected.items():
        target, temporary = core.DATA_DIR / filename, None
        try:
            try:
                with target.open("rb") as stream:
                    lines = stream.readlines()
            except FileNotFoundError:
                if rows is None:
                    continue
                raise
            if rows is not None and any(index >= len(lines) or hashlib.sha256(lines[index]).digest() != digest
                                        for index, digest in rows.items()):
                raise ValueError("记录已变化，请重新打开操作记录后重试")
            original = b"".join(lines)
            with tempfile.NamedTemporaryFile(mode="wb", dir=core.DATA_DIR,
                                             prefix=filename+".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                if rows is not None:
                    stream.writelines(line for index, line in enumerate(lines) if index not in rows)
                stream.flush()
                os.fsync(stream.fileno())
            if target.read_bytes() != original:
                raise ValueError("有新记录写入，请重新打开操作记录后重试")
            temporary.replace(target)
            deleted += len(rows) if rows is not None else len(lines)
        except (OSError, ValueError) as error:
            errors.append(f"{filename}：{error}")
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as error:
                    errors.append(f"{filename} 临时记录清理失败：{error}")
    return dict(deleted=deleted, errors=errors)


def save_feedback(text):
    text = text.strip()
    if not text:
        raise ValueError("请先填写问题描述")
    directory = core.DATA_DIR / "feedback"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (datetime.now().strftime("feedback-%Y%m%d-%H%M%S-%f") + ".txt")
    target.write_text(f"CPlus Cleaner v{VERSION}\n时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n\n{text}\n", encoding="utf-8")
    return target
