"""Record-scoped HKCU and Thunder privacy traces. Never remove user documents."""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading

from cplus_cleaner.features.privacy import privacy_browser
from cplus_cleaner.platforms import software_windows

try:
    import winreg
except ImportError:
    winreg = None

EXPLORER = r"Software\Microsoft\Windows\CurrentVersion\Explorer"
PATHS = {"run": EXPLORER + r"\RunMRU", "recent_docs": EXPLORER + r"\RecentDocs",
         "app_switched": EXPLORER + r"\FeatureUsage\AppSwitched",
         "regedit": r"Software\Microsoft\Windows\CurrentVersion\Applets\Regedit"}
_ISSUED = {}
_LOCK = threading.RLock()


def fingerprint(value):
    return hashlib.sha256(repr(value).encode('utf-8')).hexdigest()


def _issue(item):
    token = secrets.token_hex(24)
    item['token'] = token
    item['id'] = token
    with _LOCK:
        _ISSUED[token] = dict(item)
    return item


def _cancelled(cancel):
    return cancel is not None and cancel.is_set()


def _values(registry, path):
    with registry.OpenKey(registry.HKEY_CURRENT_USER, path, 0, registry.KEY_READ) as key:
        sub_count, value_count, _ = registry.QueryInfoKey(key)
        values = [registry.EnumValue(key, i) for i in range(value_count)]
        children = [registry.EnumKey(key, i) for i in range(sub_count)]
    return values, children


def scan_registry(cancel=None, progress=None, *, registry=None):
    reg = winreg if registry is None else registry
    items, diagnostics = [], []
    if reg is None:
        return {'items': [], 'diagnostics': [{'category': k, 'status': 'error', 'error': '仅支持 Windows'} for k in PATHS]}
    for category, path in PATHS.items():
        if _cancelled(cancel):
            break
        if progress:
            progress({'message': '读取 Windows 运行痕迹', 'completed': None, 'total': None})
        try:
            values, children = _values(reg, path)
            if category == 'recent_docs':
                # Native 48be70 enumerates RecentDocs child keys, not FileExts/UserChoice.
                for child in children:
                    if _cancelled(cancel):
                        break
                    child_path = path + '\\' + child
                    child_values, grandchildren = _values(reg, child_path)
                    records = []
                    for name, data, typ in child_values:
                        if name.isdecimal():
                            record = _issue(dict(kind='registry', category=category, source=child_path,
                                name=name, value=data, value_type=typ, fingerprint=fingerprint((typ, data))))
                            records.append(record)
                            items.append(record)
                    if child_values or records:
                        items.append(_issue(dict(kind='registry_group', category='extensions', source=child_path,
                            values=child_values, children=grandchildren, members=records)))
            else:
                for name, data, typ in values:
                    if ((category == 'run' and len(name) == 1 and name.isalpha()) or
                            (category == 'app_switched' and name) or
                            (category == 'regedit' and name == 'LastKey' and data)):
                        items.append(_issue(dict(kind='registry', category=category, source=path, name=name,
                            value=data, value_type=typ, fingerprint=fingerprint((typ, data)))))
            diagnostics.append(dict(category=category, status='ok', error=''))
        except FileNotFoundError:
            diagnostics.append(dict(category=category, status='missing', error='暂无记录'))
        except OSError as exc:
            diagnostics.append(dict(category=category, status='error', error=str(exc)))
    return dict(items=items, diagnostics=diagnostics, cancelled=_cancelled(cancel))


def thunder_databases():
    if winreg is None:
        return []
    found = []
    prefix = 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\'
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        for name in ('thunder_is1', '迅雷7_is1', '迅雷7', '迅雷_is1', '迅雷'):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, prefix + name, 0, winreg.KEY_READ | view) as key:
                    location, _ = winreg.QueryValueEx(key, 'InstallLocation')
                path = Path(os.path.expandvars(location)) / 'Profiles/TaskDb.dat'
                if path not in found:
                    found.append(path)
            except OSError:
                pass
    return found


def scan_thunder(cancel=None, progress=None, *, paths=None):
    items, diagnostics = [], []
    paths = thunder_databases() if paths is None else paths
    for path in paths:
        if _cancelled(cancel):
            break
        path = Path(path).absolute()
        if not path.exists():
            continue
        connection = None
        try:
            if progress:
                progress({'message': '读取迅雷下载历史', 'completed': None, 'total': None})
            identity = privacy_browser._file_identity(path)
            connection = privacy_browser._snapshot(path, cancel)
            columns = [row[1] for row in connection.execute('PRAGMA table_info(BtFile)')]
            if 'FileName' not in columns:
                raise ValueError('不支持的迅雷历史表结构')
            count = 0
            for row in connection.execute('SELECT rowid, * FROM BtFile'):
                if _cancelled(cancel):
                    break
                items.append(_issue(dict(kind='thunder', category='thunder', source=str(path), row_id=row[0],
                    fingerprint=fingerprint(tuple(row)), identity=identity)))
                count += 1
            diagnostics.append(dict(category='thunder', status='ok', count=count, error=''))
        except privacy_browser.Cancelled:
            break
        except (OSError, sqlite3.Error, ValueError, TimeoutError) as exc:
            diagnostics.append(dict(category='thunder', status='error', error=str(exc)))
        finally:
            if connection is not None:
                connection.close()
    if not diagnostics:
        diagnostics.append(dict(category='thunder', status='missing', error='暂无迅雷历史资料'))
    return dict(items=items, diagnostics=diagnostics, cancelled=_cancelled(cancel))


def _allowed(path):
    return path in (PATHS['run'], PATHS['app_switched'], PATHS['regedit']) or (
        path.startswith(PATHS['recent_docs'] + '\\') and '\\' not in path[len(PATHS['recent_docs'])+1:])


def _repair_mru(reg, path, deleted):
    """Remove stale indexes while retaining entries created after the scan."""
    with reg.OpenKey(reg.HKEY_CURRENT_USER, path, 0, reg.KEY_READ | reg.KEY_SET_VALUE) as key:
        field = 'MRUList' if path == PATHS['run'] else 'MRUListEx'
        try:
            data, typ = reg.QueryValueEx(key, field)
        except FileNotFoundError:
            return
        if field == 'MRUList' and isinstance(data, str):
            reg.SetValueEx(key, field, 0, typ, ''.join(c for c in data if c not in deleted))
        elif field == 'MRUListEx' and isinstance(data, bytes) and len(data) % 4 == 0:
            import struct
            ids = struct.unpack('<' + 'I' * (len(data)//4), data)
            retained = [i for i in ids if str(i) not in deleted and i != 0xffffffff]
            reg.SetValueEx(key, field, 0, typ, struct.pack('<' + 'I'*(len(retained)+1), *retained, 0xffffffff))


def clean_registry(items, cancel=None, *, registry=None):
    reg = winreg if registry is None else registry
    cleaned, errors, deleted = set(), [], {}
    for item in {item['id']: item for item in items}.values():
        if _cancelled(cancel):
            break
        try:
            with _LOCK:
                original = _ISSUED.get(item.get('token'))
            if original is None or original != item or item['kind'] not in ('registry', 'registry_group') or not _allowed(item['source']):
                raise ValueError('记录不属于本次扫描或超出隐私范围')
            path = item['source']
            if item['kind'] == 'registry_group':
                values, children = _values(reg, path)
                if values != item['values'] or children != item['children']:
                    raise ValueError('记录已变化，请重新扫描')
                # The only group deletion is a captured RecentDocs child key.
                with reg.OpenKey(reg.HKEY_CURRENT_USER, path, 0, reg.KEY_READ | reg.KEY_SET_VALUE) as key:
                    for name, _, _ in values:
                        if _cancelled(cancel):
                            raise privacy_browser.Cancelled()
                        reg.DeleteValue(key, name)
                        cleaned.update(member['id'] for member in item['members'] if member['name'] == name)
                        if name.isdecimal():
                            deleted.setdefault(path, set()).add(name)
                if not children:
                    reg.DeleteKey(reg.HKEY_CURRENT_USER, path)
                cleaned.update(member['id'] for member in item['members'])
            else:
                with reg.OpenKey(reg.HKEY_CURRENT_USER, path, 0, reg.KEY_READ | reg.KEY_SET_VALUE) as key:
                    value, typ = reg.QueryValueEx(key, item['name'])
                    if fingerprint((typ, value)) != item['fingerprint']:
                        raise ValueError('记录已变化，请重新扫描')
                    reg.DeleteValue(key, item['name'])
                if item['category'] in ('run', 'recent_docs'):
                    deleted.setdefault(path, set()).add(item['name'])
            cleaned.add(item['id'])
        except privacy_browser.Cancelled:
            break
        except (OSError, ValueError, TypeError) as exc:
            errors.append(dict(id=item.get('id'), error=str(exc)))
    for path, names in deleted.items():
        try:
            _repair_mru(reg, path, names)
        except FileNotFoundError:
            pass  # A completed extension-group cleanup removed this child key.
        except OSError as exc:
            errors.append(dict(id='', error=str(exc)))
    return dict(cleaned_ids=sorted(cleaned), errors=errors, cancelled=_cancelled(cancel))


def clean_thunder(items, cancel=None):
    cleaned, errors = [], []
    for item in items:
        if _cancelled(cancel):
            break
        try:
            with _LOCK:
                original = _ISSUED.get(item.get('token'))
            if original != item or item['kind'] != 'thunder':
                raise ValueError('记录不属于本次扫描')
            if any('thunder' in Path(p).name.lower() for p in software_windows.running_images()):
                raise ValueError('请先退出迅雷再清理')
            path = Path(item['source'])
            if privacy_browser._file_identity(path) != item['identity']:
                raise ValueError('资料库已替换，请重新扫描')
            with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=1)) as connection:
                connection.execute('PRAGMA trusted_schema=OFF')
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('BEGIN IMMEDIATE')
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type='trigger'").fetchone():
                    raise ValueError('迅雷资料库包含未知触发器，已保留')
                row = connection.execute('SELECT rowid, * FROM BtFile WHERE rowid=?', (item['row_id'],)).fetchone()
                if row is None or fingerprint(tuple(row)) != item['fingerprint']:
                    raise ValueError('记录已变化，请重新扫描')
                connection.execute('DELETE FROM BtFile WHERE rowid=?', (item['row_id'],))
                connection.commit()
            cleaned.append(item['id'])
        except (OSError, ValueError, sqlite3.Error) as exc:
            errors.append(dict(id=item.get('id'), error=str(exc)))
    return dict(cleaned_ids=cleaned, errors=errors, cancelled=_cancelled(cancel))

# Purpose: Windows and application usage traces
