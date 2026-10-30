"""Reconstructed registered-software inventory and publisher uninstall workflow.

Native evidence: LocalSoftEnum mode 1 (47b6a0), parser 47dbe0, file checks
47e680, recursive sizes 5475b0. Public sizes are bytes; the original bridge
truncates the directory sum to KB before displaying it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import copy
import ctypes as ct
from ctypes import wintypes as wt
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
import threading

from cplus_cleaner.common import core
from cplus_cleaner.platforms import software_windows as windows

try:
    import winreg
except ImportError:
    winreg = None

UNINSTALL = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
FIELDS = ('DisplayName', 'DisplayIcon', 'UninstallString', 'QuietUninstallString',
          'InstallLocation', 'LocationRoot', 'InstallDate', 'EstimatedSize',
          'SystemComponent', 'WindowsInstaller', 'DisplayVersion', 'Publisher')
_ISSUED = {}
_COMPLETED = {}
_LOCK = threading.RLock()


@dataclass(frozen=True)
class Software:
    key: str
    name: str
    size: int
    install_time: str
    location: str
    command: str
    icon: bytes | None = None
    source: dict = field(default_factory=dict, compare=False, repr=False)
    blocked: str = ''
    size_complete: bool = True


def _cancelled(cancel):
    return cancel is not None and cancel.is_set()


def system_directory():
    if os.name != 'nt':
        return Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32'
    kernel = ct.WinDLL('kernel32', use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [wt.LPWSTR, wt.UINT]
    buf = ct.create_unicode_buffer(32768)
    if not kernel.GetSystemDirectoryW(buf, len(buf)):
        raise ct.WinError(ct.get_last_error())
    return Path(buf.value)


def native_path(value, *, system=None):
    """47dbe0: comma first, paired quotes, special handling for MsiExec."""
    if not isinstance(value, str) or not value:
        return None
    text = value.split(',', 1)[0].strip()
    first = text.find('"')
    last = text.find('"', first+1) if first >= 0 else -1
    if 0 <= first < last:
        text = text[first+1:last]
    elif 'msiexec.' in text.lower():
        text = text.split(' ', 1)[0].lower()
        if not Path(text).is_file():
            text = str((system or system_directory())/text)
    return Path(text) if text else None


def valid_executable(path):
    return bool(path and path.suffix.lower() in ('.ico', '.exe') and path.is_file())


def _read_key(registry, source):
    hive = registry.HKEY_LOCAL_MACHINE if source['hive'] == 'HKLM' else registry.HKEY_CURRENT_USER
    view = registry.KEY_WOW64_32KEY if source['view'] == 32 else registry.KEY_WOW64_64KEY
    with registry.OpenKey(hive, UNINSTALL+'\\'+source['subkey'], 0, registry.KEY_READ | view) as key:
        values = {}
        for name in FIELDS:
            try:
                values[name] = registry.QueryValueEx(key, name)[0]
            except FileNotFoundError:
                values[name] = ''
    return values


def registry_records(cancel=None, *, registry=None):
    registry = winreg if registry is None else registry
    if registry is None:
        return [], ['软件卸载仅支持Windows']
    records, errors = {}, []
    for hive_name, hive in (('HKLM', registry.HKEY_LOCAL_MACHINE), ('HKCU', registry.HKEY_CURRENT_USER)):
        for number, view in ((32, registry.KEY_WOW64_32KEY), (64, registry.KEY_WOW64_64KEY)):
            if _cancelled(cancel):
                return list(records.values()), errors
            try:
                with registry.OpenKey(hive, UNINSTALL, 0, registry.KEY_READ | view) as key:
                    names = [registry.EnumKey(key, i) for i in range(registry.QueryInfoKey(key)[0])]
                for name in names:
                    if _cancelled(cancel):
                        break
                    source = dict(hive=hive_name, view=number, subkey=name)
                    try:
                        values = _read_key(registry, source)
                        record = dict(values, _source=source)
                        if not values.get('DisplayName') or values.get('SystemComponent') == 1:
                            continue
                        icon_path = _msi_icon(record)
                        if not icon_path and values.get('DisplayIcon'):
                            icon_path = native_path(values['DisplayIcon'])
                            if not valid_executable(icon_path):
                                continue
                        record['_native_icon'] = icon_path
                        # The native map key contains hive + key path, no view.
                        # The later 64-bit view replaces the same 32-bit key.
                        records[(hive_name, name.casefold())] = record
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        errors.append(f'{hive_name}\\{name}：{exc}')
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append(f'{hive_name} {number}位卸载信息：{exc}')
    return list(records.values()), errors


def _msi_icon(record):
    if os.name != 'nt' or record.get('WindowsInstaller') != 1:
        return None
    product = record.get('_source', {}).get('subkey', '')
    if not re.fullmatch(r'\{[0-9A-Fa-f-]{36}\}', product):
        return None
    msi = ct.WinDLL('msi', use_last_error=True)
    msi.MsiGetProductInfoW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.LPWSTR, ct.POINTER(wt.DWORD)]
    length = wt.DWORD(32767)
    buffer = ct.create_unicode_buffer(32768)
    return Path(buffer.value) if msi.MsiGetProductInfoW(product, 'ProductIcon', buffer, ct.byref(length)) == 0 and buffer.value else None


def _location(record, uninstaller):
    for field_name in ('InstallLocation', 'LocationRoot'):
        value = record.get(field_name)
        if isinstance(value, str) and value:
            path = Path(value.strip().strip('"'))
            if path.is_dir():
                return core.canonical(path)
    # 47e040 infers a directory from the original command, separately from the
    # 47dbe0 executable parser. In particular it never resolves a relative
    # MsiExec command into System32 for installation-size accounting.
    raw = str(record.get('UninstallString') or '').split(',', 1)[0].strip()
    first = raw.find('"')
    last = raw.find('"', first+1) if first >= 0 else -1
    if 0 <= first < last:
        raw = raw[first+1:last]
    parent = Path(raw).parent if raw else None
    if parent and parent != Path('.') and 'msiexec.' not in str(parent).lower() and parent.is_dir():
        return core.canonical(parent)
    return None


def _read_path(path):
    """Use extended absolute Windows paths for reads, without changing OS policy.

    Extended paths are confined to inventory I/O. Display paths, registered
    uninstall commands and the identities retained for execution stay unchanged.
    """
    value = os.path.abspath(os.fspath(path))
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return value
    return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value


def _redirected(info):
    # Keep the common walker's link protection, including compressed WOF files.
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400
        and getattr(info, 'st_reparse_tag', 0) != 0x80000017)


def _directory_size(root, cancel, errors, *, diagnostics=None):
    detail = diagnostics if diagnostics is not None else {}
    detail.update(complete=True, missing=0, unreadable=0, skipped_links=0, errors=[], missing_samples=[])
    total = 0

    def failure(path, error):
        detail['complete'] = False
        if (isinstance(error, FileNotFoundError) or getattr(error, 'winerror', None) in (2, 3)
                or getattr(error, 'errno', None) == errno.ENOENT):
            # Application updaters may remove a backup between enumeration and
            # stat. Keep the count, but do not flood the UI with vanished paths.
            detail['missing'] += 1
            if len(detail['missing_samples']) < 5:
                detail['missing_samples'].append(str(path))
            return
        detail['unreadable'] += 1
        message = f'{path}：{error}'
        if len(detail['errors']) < 5:
            detail['errors'].append(message)
        if len(errors) < 100:
            errors.append(message)

    stack = [Path(root)]
    while stack:
        if _cancelled(cancel):
            detail['complete'] = False
            break
        current = stack.pop()
        try:
            info = os.stat(_read_path(current), follow_symlinks=False)
            if _redirected(info):
                detail['skipped_links'] += 1
                continue
            with os.scandir(_read_path(current)) as entries:
                for entry in entries:
                    if _cancelled(cancel):
                        detail['complete'] = False
                        break
                    path = current / entry.name
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if _redirected(info):
                            detail['skipped_links'] += 1
                        elif stat.S_ISDIR(info.st_mode):
                            stack.append(path)
                        elif stat.S_ISREG(info.st_mode):
                            total += info.st_size
                    except OSError as exc:
                        failure(path, exc)
        except OSError as exc:
            failure(current, exc)
    detail['bytes'] = total // 1024 * 1024
    return total//1024*1024


def _date(record, uninstaller):
    value = str(record.get('InstallDate') or '')
    if len(value) == 8:
        return value
    try:
        info = uninstaller.stat()
        return datetime.fromtimestamp(getattr(info, 'st_birthtime', info.st_ctime)).strftime('%Y/%m/%d %H:%M')
    except OSError:
        return ''


def _icon_bytes(path, record):
    if not path:
        return None
    index = 0
    raw = str(record.get('DisplayIcon') or '')
    match = re.search(r',\s*(-?\d+)\s*$', raw)
    if match:
        index = int(match[1])
    try:
        return windows.extract_icon(path, index)
    except (OSError, ValueError):
        return None


def scan(cancel=None, progress=None, *, records=None, icons=True, retain_inventory=True, size_reader=None):
    if records is None:
        records, errors = registry_records(cancel)
    else:
        errors = []
    items, skipped, sizes = [], 0, {}
    issued = {}
    for index, record in enumerate(records):
        if _cancelled(cancel):
            break
        name = record.get('DisplayName', '')
        if progress:
            progress(dict(message=f'读取软件 {index+1}/{len(records)}：{name}', completed=index, total=len(records)))
        try:
            if not isinstance(name, str) or not name or record.get('SystemComponent') == 1:
                skipped += 1
                continue
            icon_path = record.get('_native_icon') if '_native_icon' in record else _msi_icon(record)
            if not icon_path and record.get('DisplayIcon'):
                icon_path = native_path(record['DisplayIcon'])
                if not valid_executable(icon_path):
                    skipped += 1
                    continue
            command = record.get('UninstallString', '')
            executable = native_path(command)
            if not command or not valid_executable(executable):
                skipped += 1
                continue
            if executable.parent.name == 'FanQieClean':
                skipped += 1
                continue
            location = _location(record, executable)
            if location is None or location == Path(location.anchor) or len(str(location)) <= 3:
                skipped += 1
                continue
            size_key = os.path.normcase(str(location))
            if size_key not in sizes:
                detail = dict(location=str(location), names=[], complete=True, missing=0,
                              unreadable=0, skipped_links=0, errors=[])
                if size_reader is None:
                    amount = _directory_size(location, cancel, errors, diagnostics=detail)
                else:
                    previous = len(errors)
                    amount = size_reader(location, cancel, errors)
                    detail.update(complete=len(errors) == previous, unreadable=len(errors)-previous,
                                  errors=errors[previous:previous+5], bytes=amount)
                sizes[size_key] = amount, detail
            if _cancelled(cancel):
                break
            amount, detail = sizes[size_key]
            source = copy.deepcopy(record.get('_source', {}))
            source['snapshot'] = {field_name: record.get(field_name, '') for field_name in FIELDS}
            source['executable'] = str(executable.absolute())
            info = executable.stat()
            source['file_identity'] = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
            identity = '\\'.join(str(source.get(k, '')) for k in ('hive', 'subkey'))
            if not identity.strip('\\'):
                identity = name+'|'+str(location)+'|'+command
            key = hashlib.sha256(identity.casefold().encode('utf-8')).hexdigest()
            item = Software(key, name, amount, _date(record, executable), str(location), command,
                            _icon_bytes(icon_path, record) if icons else None, source,
                            size_complete=detail['complete'])
            items.append(item)
            if name not in detail['names']:
                detail['names'].append(name)
            issued[key] = (item, copy.deepcopy(source))
        except (OSError, ValueError, TypeError) as exc:
            if len(errors) < 100:
                errors.append(f'{name}：{exc}')
    if retain_inventory:
        with _LOCK:
            _ISSUED.clear()
            _ISSUED.update(issued)
    return dict(items=items, errors=errors, skipped=skipped, cancelled=_cancelled(cancel),
                diagnostics=[detail for _, detail in sizes.values()])


def _validate(item):
    with _LOCK:
        issued = _ISSUED.get(item.key)
    if issued is None or issued[0] != item or issued[1] != item.source:
        raise ValueError('安装信息不属于本次扫描，请重新扫描')
    source = item.source
    if source.get('hive') not in ('HKLM', 'HKCU') or source.get('view') not in (32, 64) or not source.get('subkey') or '\\' in source['subkey']:
        raise ValueError('安装信息来源无效')
    if _read_key(winreg, source) != source['snapshot']:
        raise ValueError('安装信息已变化，请重新扫描')
    executable = Path(source['executable'])
    info = executable.stat()
    if [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns] != source['file_identity']:
        raise ValueError('卸载程序已变化，请重新扫描')
    return executable


def _still_registered(item):
    try:
        current = _read_key(winreg, item.source)
        return bool(current.get('DisplayName') or current.get('UninstallString'))
    except FileNotFoundError:
        return False


def uninstall(item, cancel=None, progress=None, *, launcher=None):
    """Wait for the publisher; no private uninstall code or guessed deletion."""
    result = dict(key=item.key, name=item.name, status='failed', error='', exit_code=None)
    if _cancelled(cancel):
        return dict(result, status='cancelled', cancelled=True)
    try:
        executable = _validate(item)
        if launcher is None:
            from cplus_cleaner.features.uninstall.uninstall_windows import launch
            launcher = launch
        process = launcher(item.command, executable)
        try:
            code = process.wait(cancel=cancel, progress=progress)
        finally:
            process.close()
        result['exit_code'] = code
        # Original checks disappearance of the original uninstaller after the
        # entire process job exits. Registry removal also confirms MSI uninstall.
        if not executable.exists() or not _still_registered(item):
            result['status'] = 'success'
            with _LOCK:
                _COMPLETED[item.key] = (item, copy.deepcopy(item.source))
                while len(_COMPLETED) > 64:
                    _COMPLETED.pop(next(iter(_COMPLETED)))
        elif code in (1602, 1223):
            result.update(status='cancelled', error='用户取消了卸载')
        elif code not in (0, None, 3010, 1641):
            result['error'] = f'卸载程序返回 {code}，安装信息仍存在'
        else:
            result.update(status='pending', error='卸载程序已结束，安装信息仍存在；请重新扫描核实')
    except OSError as exc:
        result.update(status='cancelled' if getattr(exc, 'winerror', None) == 1223 else 'failed', error=str(exc))
    except ValueError as exc:
        result['error'] = str(exc)
    result['cancelled'] = _cancelled(cancel)
    from cplus_cleaner.application import preferences
    try:
        preferences.append_activity('software_uninstall', ok=result['status'] == 'success',
            summary=f"{item.name}：{result['status']}", software=item.name, status=result['status'])
    except OSError:
        pass
    return result


def scan_residue(item, cancel=None, progress=None):
    from cplus_cleaner.features.uninstall.uninstall_residue import scan_residue as operation
    return operation(item, cancel, progress)


def clean_residue(manifest, cancel=None, progress=None):
    from cplus_cleaner.features.uninstall.uninstall_residue import clean_residue as operation
    return operation(manifest, cancel, progress)

# Purpose: Uninstall candidates, batch flow and results
