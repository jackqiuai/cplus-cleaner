"""Read-only popup software index reconstructed from PopBlkEng.dll.

REA: 0x180023190 (four uninstall stores), 0x180021e90 (entry/root),
0x180021bf0 (key leaf) and 0x180019ed0 (rule directory resolution).
This index is independent of the uninstall page's icon/executable filters.
"""
from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
import ntpath
import os
from pathlib import Path
import re

try:
    import winreg
except ImportError:
    winreg = None

UNINSTALL = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
FIELDS = ('DisplayName', 'DisplayIcon', 'UninstallString', 'InstallLocation',
          'WindowsInstaller', 'SystemComponent', 'ParentKeyName', 'ReleaseType')


def name_matches(pattern, value):
    """Native name/key glob: case-insensitive full value, *, ?, and >."""
    if not isinstance(pattern, str) or not isinstance(value, str):
        return False
    expression = ''.join('.*' if c == '*' else '[^\\\\]*' if c == '>' else '.' if c == '?'
                         else re.escape(c) for c in pattern)
    return re.fullmatch(expression, value, re.IGNORECASE | re.DOTALL) is not None


def key_name(record):
    return ntpath.basename(str(record.get('_source', {}).get('subkey', '')).rstrip('\\'))


def _expand(value):
    return os.path.expandvars(value.strip()) if isinstance(value, str) else ''


def command_image(value):
    """Parse an absolute executable without launching it or resolving PATH."""
    text = _expand(value)
    if not text or '\x00' in text:
        return None
    if text.startswith('"'):
        end = text.find('"', 1)
        if end < 0:
            return None
        image = text[1:end]
    else:
        match = re.match(r'(.+?\.(?:exe|com|bat|cmd))(?=\s|$)', text, re.IGNORECASE)
        if not match:
            return None
        image = match[1]
    path = Path(image)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        return None
    return path


def icon_location(value):
    text = _expand(value).lstrip('@')
    text = re.sub(r',\s*-?\d+\s*$', '', text).strip().strip('"')
    path = Path(text) if text else None
    return path if path and path.is_absolute() and not text.startswith(('\\\\', '//')) else None


def _directory(value):
    text = _expand(value).strip('"')
    if not text or '\x00' in text or '%' in text:
        return None
    path = Path(text)
    if not path.is_absolute() or text.startswith(('\\\\', '//')) or path.parent == path:
        return None
    return path if path.is_dir() else None


def msi_info(product, field):
    if os.name != 'nt':
        return ''
    msi = ct.WinDLL('msi', use_last_error=True)
    msi.MsiGetProductInfoW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.LPWSTR, ct.POINTER(wt.DWORD)]
    msi.MsiGetProductInfoW.restype = wt.UINT
    size = wt.DWORD(32767)
    buffer = ct.create_unicode_buffer(size.value + 1)
    result = msi.MsiGetProductInfoW(product, field, buffer, ct.byref(size))
    return buffer.value if result == 0 else ''


def index_record(values, source, *, msi_reader=msi_info):
    """Keep independently indexed records even if their icon cannot be read."""
    if (values.get('SystemComponent') == 1 or 'ParentKeyName' in values or 'ReleaseType' in values
            or not isinstance(values.get('DisplayName'), str) or not values['DisplayName'].strip()
            or not isinstance(values.get('UninstallString'), str) or not values['UninstallString'].strip()):
        return None
    record = dict(values, _source=dict(source), _popup_index=True)
    command = values['UninstallString']
    image = command_image(command)
    # A WindowsInstaller entry must contain the MSI product command, as in the
    # native parser. The MSI host directory is never an application root.
    product = re.search(r'\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}', command)
    is_msi_host = bool(re.search(r'(?:^|[\\/"\s])msiexec(?:\.exe)?(?:["\s]|$)', command, re.I))
    if values.get('WindowsInstaller') == 1:
        if not is_msi_host or not product:
            return None
        root = _directory(msi_reader(product[0], 'InstallLocation'))
        record['_native_icon'] = icon_location(msi_reader(product[0], 'ProductIcon'))
    else:
        root = _directory(values.get('InstallLocation'))
        record['_native_icon'] = icon_location(values.get('DisplayIcon'))
    if root is None and not is_msi_host and image is not None:
        # Do not require a surviving uninstaller executable; the native parser
        # validates the resulting directory, rather than the command's file.
        root = _directory(str(image.parent))
    if root is None:
        return None
    record['_popup_root'] = str(root)
    return record


def registry_records(cancel=None, *, registry=None, msi_reader=msi_info):
    registry = winreg if registry is None else registry
    if registry is None:
        return [], ['弹窗软件索引仅支持Windows']
    records, errors = [], []
    # Native list order is significant: keep both views, do not let a 64-bit
    # entry overwrite a same-key 32-bit entry or conflate HKCU with HKLM.
    stores = ((32, 'HKLM'), (32, 'HKCU'), (64, 'HKLM'), (64, 'HKCU'))
    for number, hive_name in stores:
        if cancel is not None and cancel.is_set():
            break
        hive = registry.HKEY_LOCAL_MACHINE if hive_name == 'HKLM' else registry.HKEY_CURRENT_USER
        view = registry.KEY_WOW64_32KEY if number == 32 else registry.KEY_WOW64_64KEY
        try:
            with registry.OpenKey(hive, UNINSTALL, 0, registry.KEY_READ | view) as parent:
                names = [registry.EnumKey(parent, i) for i in range(registry.QueryInfoKey(parent)[0])]
            for name in names:
                if cancel is not None and cancel.is_set():
                    break
                try:
                    values = {}
                    with registry.OpenKey(hive, UNINSTALL+'\\'+name, 0, registry.KEY_READ | view) as key:
                        for field in FIELDS:
                            try:
                                values[field] = registry.QueryValueEx(key, field)[0]
                            except FileNotFoundError:
                                pass
                    source = dict(hive=hive_name, view=number, subkey=name)
                    record = index_record(values, source, msi_reader=msi_reader)
                    if record is not None:
                        records.append(record)
                except FileNotFoundError:
                    pass
                except OSError as error:
                    errors.append(f'{hive_name} {number}位\\{name}：{error}')
        except FileNotFoundError:
            pass
        except OSError as error:
            errors.append(f'{hive_name} {number}位弹窗软件索引：{error}')
    return records, errors
