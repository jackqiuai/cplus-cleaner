"""Version/layout-aware chat cleanup with retained deletion capabilities.

Only catalogued cache/media subdirectories are enumerated. Account stores are
never parsed. Unknown layouts are reported, not treated as empty caches.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import uuid

import core
import preferences
import software_windows

CATALOG = json.loads((core.APP_DIR / 'assets/chat-rules.json').read_text(encoding='utf-8'))['applications']
APPS = {app['id']: app for app in CATALOG}
CHROMIUM = ('Cache/Cache_Data', 'Code Cache', 'GPUCache')
PROTECTED_SUFFIXES = ('.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3', '.sqlite-wal',
                      '.sqlite-shm', '.key', '.json', '.ini', '.bak', '.backup', '.db-journal')
PROTECTED_NAMES = frozenset(('cookies', 'login data', 'preferences', 'local state',
                             'config', 'config.bin', 'key_data', 'settings', 'settingss'))
_SCANS = {}
_LOCK = threading.RLock()


def path_key(path):
    return os.path.normcase(os.path.abspath(path))


def identity(item):
    return (item.device, item.inode) if item.inode else path_key(item.path)


def locations():
    home = Path.home()
    documents = home / 'Documents'
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
            documents = Path(os.path.expandvars(winreg.QueryValueEx(key, 'Personal')[0]))
    except (ImportError, OSError):
        pass
    return dict(documents=documents, roaming=Path(os.environ.get('APPDATA', home/'AppData/Roaming')),
                local=Path(os.environ.get('LOCALAPPDATA', home/'AppData/Local')))


def load_paths():
    try:
        values = json.loads((core.DATA_DIR/'chat-paths.json').read_text(encoding='utf-8'))
        return {key: value for key, value in values.items() if key in APPS and isinstance(value, str) and value}
    except (OSError, ValueError, AttributeError):
        return {}


def save_path(app_id, path=None):
    if app_id not in APPS:
        raise ValueError('未知聊天软件')
    values = load_paths()
    if path:
        folder = core.check_path(Path(path))
        if not folder.is_dir():
            raise ValueError('聊天数据目录不存在')
        values[app_id] = str(folder)
    else:
        values.pop(app_id, None)
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = core.DATA_DIR/'chat-paths.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)


def installed_records(cancel=None):
    # Read chat entries directly. The uninstall page also resolves MSI icons;
    # that unrelated work must not turn registry ACLs into chat-file failures.
    try:
        import winreg
    except ImportError:
        return [], []
    from software_uninstall import native_path
    records, warnings, seen = [], [], set()
    unreadable = 0
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 0,
                                    winreg.KEY_READ | view) as root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        if cancel and cancel.is_set():
                            return records, warnings
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, index)) as key:
                                name = str(winreg.QueryValueEx(key, 'DisplayName')[0])
                                if not any(_matching_records(spec, [dict(DisplayName=name)]) for spec in CATALOG):
                                    continue
                                values = dict(DisplayName=name)
                                for field in ('DisplayVersion', 'DisplayIcon', 'InstallLocation'):
                                    try:
                                        values[field] = winreg.QueryValueEx(key, field)[0]
                                    except OSError:
                                        values[field] = ''
                                identity = (name.casefold(), values['InstallLocation'])
                                if identity not in seen:
                                    seen.add(identity)
                                    values['_native_icon'] = native_path(values['DisplayIcon'])
                                    records.append(values)
                        except FileNotFoundError:
                            pass
                        except OSError:
                            unreadable += 1
            except FileNotFoundError:
                pass
            except OSError as exc:
                warnings.append(str(exc))
    if unreadable:
        warnings.append('部分安装信息无法读取；仍会继续检测已知聊天数据目录')
    return records, warnings


def _matching_records(spec, records):
    def matches(record):
        name = str(record.get('DisplayName', '')).casefold()
        if spec['id'] == 'qq':
            return name == 'qq' or name.startswith(('qq ', '腾讯qq', 'qq9', 'qqnt'))
        return any(alias in name for alias in spec['aliases'])
    return [record for record in records if matches(record)]


def _safe_directory(path, errors):
    try:
        path = core.check_path(path)
        return path if path.is_dir() else None
    except (OSError, ValueError) as exc:
        errors.append(dict(root=str(path), error=str(exc)))
        return None


def _children(path, errors):
    try:
        return [child for child in path.iterdir() if child.is_dir() and not core.is_link(child)]
    except OSError as exc:
        errors.append(dict(root=str(path), error=str(exc)))
        return []


def _roots(spec, bases, paths, errors):
    if spec['id'] in paths:
        candidates = [Path(paths[spec['id']])]
    else:
        candidates = [bases[base]/suffix for base, suffix in spec.get('locations', [])]
        packages = bases['local']/'Packages'
        if spec.get('packages') and _safe_directory(packages, errors):
            candidates.extend(child for child in _children(packages, errors)
                              if any(child.name.casefold().startswith(prefix.casefold()) for prefix in spec['packages']))
        if spec['id'] == 'wechat':
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Tencent\WeChat') as key:
                    value = winreg.QueryValueEx(key, 'FileSavePath')[0]
                    if value and value != 'MyDocument:':
                        folder = Path(os.path.expandvars(value))
                        candidates.extend([folder, folder/'WeChat Files', folder/'xwechat_files'])
            except (ImportError, OSError):
                pass
    roots, seen = [], set()
    for path in candidates:
        if path_key(path) in seen:
            continue
        seen.add(path_key(path))
        safe = _safe_directory(path, errors)
        if safe:
            roots.append(safe)
    return roots


def discover(*, paths=None, bases=None, records=None, cancel=None, icons=True):
    bases = {key: Path(value) for key, value in (locations() if bases is None else bases).items()}
    paths = load_paths() if paths is None else dict(paths)
    records, problems = installed_records(cancel) if records is None else (records, [])
    result = []
    for spec in CATALOG:
        if cancel and cancel.is_set():
            break
        errors = []
        roots = _roots(spec, bases, paths, errors)
        matched = _matching_records(spec, records)
        webapp = any('web applications' in str(record.get('DisplayIcon', '')).casefold() or
                     '网页版' in str(record.get('DisplayName', '')) for record in matched)
        icon = None
        if icons and matched:
            from software_uninstall import native_path
            icon_path = matched[0].get('_native_icon') or native_path(matched[0].get('DisplayIcon', ''))
            try:
                icon = software_windows.extract_icon(icon_path)
            except OSError:
                pass
        result.append(dict(id=spec['id'], name=spec['name'], roots=roots, icon=icon,
                           variant='web' if webapp else 'desktop',
                           version=' / '.join(dict.fromkeys(str(record.get('DisplayVersion', '')) for record in matched
                                                            if record.get('DisplayVersion'))),
                           installed=bool(matched), status='detected' if roots else 'unreadable' if errors else
                           'unsupported' if matched else 'missing', errors=errors, accounts=0))
    return result, list(problems)


def _protected(path):
    return path.name.casefold() in PROTECTED_NAMES or path.name.casefold().endswith(PROTECTED_SUFFIXES)


def _app_rules(state, days, errors):
    spec = APPS[state['id']]
    rules, metadata, seen, accounts = [], {}, set(), set()
    for root in state['roots']:
        parents = [root]
        if spec.get('accounts'):
            children = _children(root, errors)
            parents.extend(children)
            # QQ NT can add an account-container level. Only account markers
            # below these bounded directory levels are ever scanned.
            for child in children:
                if child.name.casefold() in ('nt_qq', 'users', 'accounts', 'all_users'):
                    parents.extend(_children(child, errors))
        for parent in parents:
            layouts = list(spec['rules'])
            for profile in spec.get('chromium', []):
                layouts.extend([((Path(profile)/suffix).as_posix(), '缓存文件', True) for suffix in CHROMIUM])
            for suffix, name, default in layouts:
                folder = parent/suffix
                if path_key(folder) in seen:
                    continue
                safe = _safe_directory(folder, errors)
                if safe is None:
                    continue
                seen.add(path_key(folder))
                if spec.get('accounts') and suffix not in CHROMIUM:
                    accounts.add(path_key(parent))
                source = f"{spec['name']} / {name}"
                rules.append(core.Rule(source, safe, spec['name'], days))
                metadata[source] = dict(app=spec['id'], name=name, group=spec['name'], default=default)
    state['accounts'] = len(accounts)
    return rules, metadata


def scan(cancel=None, progress=None, *, days=0, paths=None, bases=None, records=None, icons=True):
    if not isinstance(days, int) or days < 0:
        raise ValueError('扫描天数无效')
    cancel = cancel if cancel is not None else threading.Event()
    states, warnings = discover(paths=paths, bases=bases, records=records, cancel=cancel, icons=icons)
    errors = []
    items, entries, rules, defaults, details, seen, issued = [], [], [], set(), [], set(), {}
    def report(message, **extra):
        if progress:
            progress(dict(message=message, **extra))
    for index, state in enumerate(states):
        if cancel.is_set():
            break
        report(f"正在扫描 {state['name']}…", completed=index, total=len(CATALOG), value=sum(i.size for i in items))
        app_rules, metadata = _app_rules(state, days, state['errors'])
        if app_rules:
            state['status'] = 'ready' if state['installed'] else 'data'
        elif state['roots']:
            state['status'] = 'unreadable' if state['errors'] else 'unsupported'
        for source, meta in metadata.items():
            entries.append(dict(index=len(entries), **meta))
            if meta['default']:
                defaults.add(source)
        detail_start = len(details)
        found = core.scan_rules(app_rules, cancel,
            lambda message: report(f"正在扫描 {state['name']}…", completed=index, total=len(CATALOG)), diagnostics=details)
        if state['errors'] or any(detail.get('errors') for detail in details[detail_start:]):
            state['status'] = 'unreadable'
        for item in found:
            if _protected(Path(item.path)) or identity(item) in seen:
                continue
            seen.add(identity(item))
            items.append(item)
            issued[path_key(item.path)] = (item, state['id'])
        rules.extend(app_rules)
        report(f"已核对 {state['name']}", completed=index+1, total=len(CATALOG), value=sum(i.size for i in items))
    for state in states:
        errors.extend(state['errors'])
    errors.extend(detail for detail in details if detail.get('errors'))
    token = uuid.uuid4().hex
    with _LOCK:
        _SCANS[token] = issued
        while len(_SCANS) > 32:
            _SCANS.pop(next(iter(_SCANS)))
    return dict(scan_id=token, items=items, rules=rules, manifest_items=entries, defaults=defaults,
                apps=states, diagnostics=details, errors=errors, discovery_warnings=warnings,
                cancelled=cancel.is_set(), days=days)


def volume_totals(items):
    totals, seen = {}, set()
    for item in items:
        if identity(item) in seen:
            continue
        seen.add(identity(item))
        drive = Path(item.path).drive.upper() or Path(item.path).anchor
        totals[drive] = totals.get(drive, 0) + item.size
    return totals


def delete(snapshot, items, cancel=None, progress=None):
    """Recheck the issued snapshot and live process state, then use core's checks."""
    with _LOCK:
        issued = dict(_SCANS.get(snapshot.get('scan_id'), {}))
    process_error = ''
    try:
        running = {path.name.casefold() for path in software_windows.running_images()}
    except OSError:
        running, process_error = set(), '无法确认聊天软件运行状态，已跳过'
    blocked = {spec['id'] for spec in CATALOG if running.intersection(spec['executables'])}
    valid, errors, seen = [], [], set()
    for item in items:
        try:
            saved = issued.get(path_key(item.path))
            if not saved or saved[0] != item:
                raise ValueError('扫描凭据无效或文件不在本次聊天清理范围，请重新扫描')
            if process_error or saved[1] in blocked:
                raise ValueError(process_error or '软件正在运行，已跳过；退出该聊天软件后可重试')
            if _protected(Path(item.path)):
                raise ValueError('受保护的聊天数据')
            core.validate_candidate(item)
            if identity(item) not in seen:
                seen.add(identity(item))
                valid.append(item)
        except (OSError, ValueError) as exc:
            errors.append(dict(path=item.path, error=str(exc)))
    result = core.delete_files(valid, cancel, progress) if valid else dict(deleted=0, bytes=0, errors=[], cancelled=bool(cancel and cancel.is_set()))
    result['errors'].extend(errors)
    preferences.append_activity('chat_cleanup', ok=not result['errors'] and not result['cancelled'],
                                summary=f"聊天清理：删除 {result['deleted']} 项，释放 {result['bytes']} 字节，跳过 {len(result['errors'])} 项",
                                count=result['deleted'], bytes=result['bytes'])
    return result
