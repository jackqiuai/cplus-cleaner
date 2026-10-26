"""Recovered popup catalog, installed-software discovery and confirmed blocking.

Only the complete Huorong catalog is loaded. Every rule is bound to observed
installation roots and must satisfy all of its supported conditions.
Selected window actions are revalidated and executed by the Win32 adapter.
"""
from __future__ import annotations

from dataclasses import replace
import json
import ntpath
import os
import re
from pathlib import Path
import tempfile
import time

from cplus_cleaner.common import core
from cplus_cleaner.features.popup import popup_windows as windows
from cplus_cleaner.features.uninstall import software_uninstall as installed
from cplus_cleaner.platforms import software_windows
from cplus_cleaner.features.popup.popup_models import Rule, Software
from cplus_cleaner.features.popup import popup_huorong as huorong
from cplus_cleaner.features.popup import popup_inventory as inventory


CATALOG = huorong.CATALOG
HISTORY_DAYS = 30
DEFAULT_ACTIONS = dict.fromkeys(('hide', 'kill', 'close_hide'), True)


def _icon_location(value):
    if not value:
        return None
    text = os.path.expandvars(str(value)).strip().lstrip('@')
    match = re.fullmatch(r'"([^"]+)"(?:\s*,\s*(-?\d+))?', text)
    if match:
        text, number = match.group(1), match.group(2)
    else:
        text, comma, tail = text.rpartition(',')
        if comma and re.fullmatch(r'\s*-?\d+\s*', tail):
            number = tail
        else:
            text, number = str(value).strip().strip('"'), None
            text = os.path.expandvars(text).lstrip('@')
    path = Path(text)
    if not path.is_absolute() or str(path).startswith('\\\\'):
        return None
    return path, int(number) if number else 0


def _vendor_icon(item, records, cache):
    """Resolve the vendor's icon hint, then registered installation resources."""
    hint = item.definition.get('display_icon', {})
    candidates, matches = [], []
    if isinstance(hint, dict):
        if hint.get('file_path'):
            for root in item.roots or ('',):
                try:
                    candidates.append(huorong.expand(hint['file_path'], root))
                except huorong.Unsupported:
                    pass
        if hint.get('appname'):
            matches = [record for record in records if huorong._name_matches(hint['appname'], record.get('DisplayName', ''))]
    condition = item.definition.get('exist', {})
    for record in records:
        name_match = condition.get('appname') and huorong._name_matches(condition['appname'], record.get('DisplayName', ''))
        key_match = condition.get('appkey') and huorong._name_matches(condition['appkey'], inventory.key_name(record))
        if name_match or key_match:
            if record not in matches:
                matches.append(record)
    if not matches and item.roots:
        for record in records:
            try:
                if {_path(root) for root in _roots(record)}.intersection(_path(root) for root in item.roots):
                    matches.append(record)
            except OSError:
                continue
    for record in matches:
        candidates.extend((record.get('DisplayIcon'), record.get('_native_icon'),
                           installed.native_path(record.get('UninstallString', ''))))
    for value in candidates:
        try:
            location = _icon_location(value)
            if location is None:
                continue
            path, index = location
            key = (_path(path), index)
            if key not in cache:
                cache[key] = software_windows.extract_icon(path, index) if path.is_file() else None
            if cache[key]:
                return cache[key]
        except (OSError, ValueError):
            continue
    return None


def catalog():
    """The complete, sole runtime catalog: 200 Huorong groups / 769 definitions."""
    return huorong.catalog()


def visible_items(items):
    """Keep the complete catalog on disk; present only actionable definitions."""
    result = []
    for item in items:
        if item.source != huorong.SOURCE:
            continue
        rules = tuple(r for r in item.rules if r.source == huorong.SOURCE
                      and r.supported and r.matcher is not None)
        if rules:
            result.append(replace(item, rules=rules))
    return result


def _roots(record):
    result = set()
    def admit(path):
        path = Path(path)
        if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
            return
        candidates = (str(path), str(path.resolve()))
        shared = {_path(os.environ.get(name, default)) for name, default in (
            ('ProgramFiles', r'C:\Program Files'), ('ProgramFiles(x86)', r'C:\Program Files (x86)'),
            ('ProgramData', r'C:\ProgramData'))}
        system = os.environ.get('SystemRoot', r'C:\Windows')
        if (not path.is_absolute() or not path.is_dir() or path.parent == path
                or any(_path(value) in shared or owned(value, (system,)) for value in candidates)):
            return
        result.update(candidates)
    if record.get('_popup_index'):
        if record.get('_popup_root'):
            admit(record['_popup_root'])
        return result
    for field in ('InstallLocation', 'LocationRoot'):
        value = record.get(field)
        if value:
            path = Path(os.path.expandvars(str(value).strip('" ')))
            admit(path)
    for value in (record.get('_native_icon'), installed.native_path(record.get('UninstallString', ''))):
        if value:
            path = Path(value)
            if path.is_absolute() and path.is_file():
                admit(path.parent)
    return result


def scan(cancel=None, progress=None, *, stage_progress=None):
    """Read registry names/icons; no directory traversal or active interception."""
    def report(message, completed=0, total=None):
        if stage_progress:
            stage_progress(dict(message=message, completed=completed, total=total))

    report('正在读取已安装软件信息…')
    definitions = catalog()
    vendor_reason = windows.vendor_support_reason() if any(d.source == huorong.SOURCE for d in definitions) else ''
    records, errors = inventory.registry_records(cancel)
    items, icon_cache = [], {}
    total = len(definitions)
    report('正在核对软件弹窗规则…', 0, total)
    for index, definition in enumerate(definitions, 1):
        if cancel is not None and cancel.is_set():
            break
        item = huorong.discover(definition, records, _roots)
        if item is not None:
            if vendor_reason:
                item = huorong.disable_group(item, vendor_reason)
            visible = visible_items([item])
            if visible:
                item = visible[0]
                items.append(replace(item, icon=_vendor_icon(item, records, icon_cache)))
            if visible and progress:
                progress(f'已发现 {len(items)} 个软件分组、{sum(len(s.rules) for s in items)} 项弹窗规则')
        report(f'已核对 {index}/{total} 个规则分组', index, total)
    return dict(items=items, errors=errors, cancelled=bool(cancel and cancel.is_set()))


def _path(value):
    return ntpath.normcase(ntpath.normpath(str(value)))


def owned(image, roots):
    image = _path(image)
    if not ntpath.isabs(image):
        return False
    for root in roots:
        root = _path(root)
        try:
            if ntpath.isabs(root) and ntpath.dirname(root) != root and ntpath.commonpath((image, root)) == root:
                return True
        except ValueError:
            continue
    return False


def matches(rule, item, software):
    return bool(software.source == huorong.SOURCE and rule.source == huorong.SOURCE
                and owned(item.get('image', ''), software.roots) and huorong.matches(rule, item))


def _directory():
    return core.DATA_DIR / 'popup'


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name+'.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Blocker:
    def __init__(self):
        self.items, self.pending = [], {}
        self.error = ''
        self.state = dict(version=3, enabled=False, software={}, rules={}, counts={}, history={},
                          actions=dict(DEFAULT_ACTIONS))
        try:
            values = json.loads((_directory()/'state.json').read_text('utf-8'))
            if not isinstance(values, dict) or type(values.get('enabled')) is not bool:
                raise ValueError('弹窗设置格式无效')
            for field in ('software', 'rules', 'counts', 'history'):
                if not isinstance(values.get(field, {}), dict):
                    raise ValueError('弹窗设置格式无效')
            if any(type(value) is not bool for field in ('software', 'rules') for value in values.get(field, {}).values()):
                raise ValueError('弹窗开关格式无效')
            if any(type(value) is not int or value < 0 for value in values.get('counts', {}).values()):
                raise ValueError('弹窗计数格式无效')
            actions = values.get('actions', {})
            version = values.get('version', 1)
            if type(version) is not int or version < 1:
                raise ValueError('弹窗设置版本无效')
            if (not isinstance(actions, dict) or set(actions) - {'hide', 'kill', 'close_hide'}
                    or any(type(value) is not bool for value in actions.values())):
                raise ValueError('弹窗处理动作开关格式无效')
            self.state.update({key: values[key] for key in self.state if key in values})
            self.state['actions'] = dict(DEFAULT_ACTIONS) if version < 3 else DEFAULT_ACTIONS | actions
            self.state['version'] = 3
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as error:
            self.error = f'弹窗设置未载入：{error}'
        self._dirty = False

    @property
    def enabled(self):
        return self.state['enabled']

    @property
    def total_count(self):
        return sum(self.state['counts'].values())

    def set_items(self, items):
        self.items = [replace(item, rules=tuple(rule for rule in item.rules if rule.source == huorong.SOURCE))
                      for item in items if item.source == huorong.SOURCE]

    def group_enabled(self, item):
        default = any(rule.default_enabled and rule.supported for rule in item.rules)
        return self.state['software'].get(item.key, default)

    def action_allowed(self, action):
        return action == 'close' or self.state['actions'].get(action, False)

    def rule_available(self, rule):
        return rule.source == huorong.SOURCE and rule.supported and rule.matcher is not None and self.action_allowed(rule.action)

    def rule_enabled(self, rule):
        return self.rule_available(rule) and self.state['rules'].get(rule.id, rule.default_enabled)

    def selected_count(self):
        return sum(self.rule_enabled(rule) for item in self.items if self.group_enabled(item) for rule in item.rules)

    def rule_count(self, rule):
        return self.state['counts'].get(rule.id, 0)

    def group_count(self, item):
        return sum(self.rule_count(rule) for rule in item.rules)

    def _commit(self, update):
        state = dict(self.state, **{key: dict(self.state[key]) for key in ('software', 'rules', 'counts', 'history', 'actions')})
        update(state)
        _atomic(_directory()/'state.json', state)
        self.state, self.error, self._dirty = state, '', False

    def enable_detected(self):
        """Enable scanned software and supported window-only actions.

        Process termination remains an explicit per-rule choice. A completed
        scan never newly opts in to kill, nor resets action permissions.
        """
        def update(state):
            enabled = False
            for item in self.items:
                available = False
                for rule in item.rules:
                    allowed = self.rule_available(rule)
                    state['rules'][rule.id] = bool(allowed and
                        (rule.action in ('close', 'hide', 'close_hide')
                         or state['rules'].get(rule.id, False)))
                    available = available or allowed
                    enabled = enabled or state['rules'][rule.id]
                state['software'][item.key] = available
            state['enabled'] = enabled
        self._commit(update)

    def set_software(self, item, enabled):
        def update(state):
            state['software'][item.key] = bool(enabled)
            fallback = not any(self.rule_available(r) and r.action == 'close' for r in item.rules)
            for rule in item.rules:
                state['rules'][rule.id] = self._bulk_selection(rule, enabled, fallback=fallback)
            if enabled:
                state['enabled'] = True
        self._commit(update)

    def set_rule(self, rule, enabled):
        if enabled and not self.rule_available(rule):
            raise ValueError(rule.reason or '请先单独允许此处理动作')
        def update(state):
            state['rules'][rule.id] = bool(enabled)
            for item in self.items:
                if rule in item.rules:
                    state['software'][item.key] = any(state['rules'].get(child.id, child.default_enabled) for child in item.rules)
            if enabled:
                state['enabled'] = True
        self._commit(update)

    def set_all(self, enabled):
        def update(state):
            state['enabled'] = bool(enabled)
            for item in self.items:
                state['software'][item.key] = bool(enabled)
                fallback = not any(self.rule_available(r) and r.action == 'close' for r in item.rules)
                for rule in item.rules:
                    state['rules'][rule.id] = self._bulk_selection(rule, enabled, fallback=fallback)
        self._commit(update)

    def _bulk_selection(self, rule, enabled, *, fallback=False):
        # Prefer close. Hide-only groups (e.g. LDPlayer9) need their supported
        # window actions when explicitly enabled; never newly select kill.
        return bool(enabled and self.rule_available(rule) and
                    (rule.action == 'close' or self.state['rules'].get(rule.id, False)
                     or fallback and rule.action in ('hide', 'close_hide')))

    def set_action_allowed(self, action, allowed):
        if action not in ('hide', 'kill', 'close_hide') or type(allowed) is not bool:
            raise ValueError('处理动作开关无效')
        self._commit(lambda state: state['actions'].__setitem__(action, allowed))

    @staticmethod
    def _token(item):
        return (item['hwnd'], item['pid'], item['created'])

    def poll(self):
        recovered = self._dirty
        if self._dirty:
            _atomic(_directory()/'state.json', self.state)
            self._dirty = False
        if not self.enabled and not self.pending:
            return recovered
        current = windows.snapshot()
        live = {self._token(item) for item in current}
        now, changed = time.time(), recovered
        for token, pending in list(self.pending.items()):
            software, rule, observed, snapshot = pending
            if token not in live and windows.disappeared(snapshot):
                count = self.rule_count(rule)+1
                self.state['counts'][rule.id] = count
                self.state['history'][rule.id] = dict(time=now, software=software.name, title=rule.label,
                                                     rule_id=rule.id, count=count, source=rule.source,
                                                     action=rule.action)
                del self.pending[token]
                changed = self._dirty = True
            elif now-observed > 15:
                del self.pending[token]
        if self._dirty:
            _atomic(_directory()/'state.json', self.state)
            self._dirty = False
        if self.enabled:
            killed = {(pending[3]['pid'], pending[3]['created']) for pending in self.pending.values()
                      if pending[1].action == 'kill'}
            for item in current:
                token = self._token(item)
                if token in self.pending or (item['pid'], item['created']) in killed:
                    continue
                candidates = [(software, rule) for software in self.items if self.group_enabled(software)
                              and owned(item.get('image', ''), software.roots)
                              for rule in software.rules if self.rule_enabled(rule)]
                extended = None
                attempted = False
                for software, rule in candidates:
                    observed = item
                    if not attempted:
                        extended = windows.enrich(item)
                        attempted = True
                    if extended is None:
                        continue
                    observed = extended
                    if rule.matcher.points:
                        if not huorong.matches(rule, observed, check_pixels=False):
                            continue
                        points = tuple(point[:2] for point in rule.matcher.points)
                        observed = windows.enrich(item, points)
                        if observed is None:
                            continue
                    if not matches(rule, observed, software):
                        continue
                    points = tuple(point[:2] for point in rule.matcher.points)
                    def validate(fresh, software=software, rule=rule):
                        return (self.enabled and self.group_enabled(software) and self.rule_enabled(rule)
                                and matches(rule, fresh, software))
                    accepted = windows.apply_vendor(item, rule.action, validate, points)
                    if accepted:
                        self.pending[token] = (software, rule, now, dict(item))
                        if rule.action == 'kill':
                            killed.add((item['pid'], item['created']))
                        break
        return changed

    def history(self):
        cutoff = time.time()-HISTORY_DAYS*86400
        ids = {rule.id for item in self.items for rule in item.rules}
        result = []
        for identity, row in self.state['history'].items():
            if identity not in ids or not isinstance(row, dict):
                continue
            timestamp = row.get('time')
            if isinstance(timestamp, (int, float)) and timestamp >= cutoff:
                result.append(dict(row, count=self.state['counts'].get(identity, 0)))
        return sorted(result, key=lambda row: row['time'], reverse=True)

    def clear_history(self):
        self._commit(lambda state: state.update(history={}))

# Purpose: Popup scan and blocking orchestration
