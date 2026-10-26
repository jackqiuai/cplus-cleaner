"""Bounded original residual candidates, with independent snapshot protection."""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import secrets
import stat
import sys
import threading

from cplus_cleaner.common import core, runtime
from cplus_cleaner.features.uninstall import software_uninstall as installed

_MANIFESTS = {}
_LOCK = threading.RLock()
_GENERIC = {'microsoft','windows','programs','program files','program files (x86)',
            'common files','software','appdata','roaming','local','temp','installer','users','system32','syswow64'}


def roots():
    profile = Path.home()
    system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    local = Path(os.environ.get('LOCALAPPDATA', profile/'AppData/Local'))
    roaming = Path(os.environ.get('APPDATA', profile/'AppData/Roaming'))
    data = Path(os.environ.get('ProgramData', r'C:\ProgramData'))
    return dict(local=local, roaming=roaming, data=data,
        programs=Path(system.anchor)/'Program Files',
        programs_x86=Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')),
        common_start=data/'Microsoft/Windows/Start Menu',
        user_start=roaming/'Microsoft/Windows/Start Menu')


def _component(name):
    return bool(name and name not in ('.','..') and not name.endswith(('.', ' ')) and
        not any(c in name for c in '\\/:*?"<>|\0') and name.casefold() not in _GENERIC)


def _completed(item):
    with installed._LOCK:
        record = installed._COMPLETED.get(item.key)
    if record is None or record[0] != item or record[1] != item.source:
        raise ValueError('只能清理本次已确认卸载软件的残留')
    executable = Path(item.source['executable'])
    try:
        current = installed._read_key(installed.winreg, item.source)
    except FileNotFoundError:
        current = None
    if current is not None and current != item.source['snapshot']:
        raise ValueError('安装信息已变化或软件已重新安装，已保留残留')
    if executable.exists() and current is not None:
        raise ValueError('软件安装信息仍存在或软件已重新安装，已保留残留')


def _path_guard(path, other_roots):
    path = core.canonical(path)
    protected = list(map(core.canonical, runtime.protected_paths(core.APP_DIR)))
    if not runtime.frozen():
        protected.append(core.canonical(Path(sys.executable).parent))
    if any(core.inside(path, root) or core.inside(root, path) for root in protected):
        return '属于当前运行程序'
    if any(core.inside(path, root) or core.inside(root, path) for root in other_roots):
        return '与其他已安装软件共享路径'
    for part in (path, *path.parents):
        if part.exists() and core.is_link(part):
            return '路径包含链接或重解析项'
    return ''


def _file_snapshot(path, base, blocked=''):
    info = path.lstat()
    blocked = blocked or ('文件有其他硬链接' if info.st_nlink > 1 else '')
    return dict(path=str(core.canonical(path)), size=info.st_size, root=str(base), blocked=blocked,
                identity=[info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns])


def _registry_snapshot(registry, hive, path, view):
    root = registry.HKEY_LOCAL_MACHINE if hive == 'HKLM' else registry.HKEY_CURRENT_USER
    flag = registry.KEY_WOW64_32KEY if view == 32 else registry.KEY_WOW64_64KEY
    with registry.OpenKey(root, path, 0, registry.KEY_READ | flag) as key:
        sub_count, val_count, modified = registry.QueryInfoKey(key)
        children = [registry.EnumKey(key, i) for i in range(sub_count)]
        values = [registry.EnumValue(key, i) for i in range(val_count)]
    digest = hashlib.sha256(repr((children, values, modified)).encode('utf-8')).hexdigest()
    return digest, children


def scan_residue(item, cancel=None, progress=None, *, bases=None, registry=None, others=None):
    _completed(item)
    bases = roots() if bases is None else bases
    registry = installed.winreg if registry is None else registry
    files, keys, errors = [], [], []
    leaf = Path(item.source['executable']).parent.name
    if not _component(leaf):
        return dict(files=[], registry=[], errors=['卸载目录名称不适合作为残留范围'])
    verification = [Path(bases[k])/leaf for k in ('programs','programs_x86','local','roaming')]
    verification += [Path(bases[k])/'Programs'/leaf for k in ('programs','programs_x86','local','roaming')]
    if not any(path.exists() for path in verification):
        return dict(files=[], registry=[], errors=[])
    if others is None:
        with installed._LOCK:
            others = [record[0] for key, record in installed._ISSUED.items() if key != item.key]
    other_roots = [core.canonical(Path(other.location)) for other in others if other.location]
    directories = [Path(bases[k])/leaf for k in ('local','roaming','data','programs','programs_x86')]
    if _component(item.name):
        directories += [Path(bases[k])/'Programs'/item.name for k in ('common_start','user_start')]
    seen = set()
    def failure(path, error):
        if len(errors) < 100:
            errors.append(f'{path}：{error}')
    for directory in directories:
        if installed._cancelled(cancel):
            break
        if progress:
            progress({'message': '扫描关联目录：'+str(directory)})
        try:
            if not directory.is_dir():
                continue
            reason = _path_guard(directory, other_roots)
            if reason:
                errors.append(str(directory)+'：'+reason)
                continue
            for path in core.walk_files(directory, cancel, on_error=failure):
                name = os.path.normcase(str(core.canonical(path)))
                if name in seen:
                    continue
                try:
                    snapshot = _file_snapshot(path, directory)
                    if snapshot['size']:
                        files.append(snapshot)
                        seen.add(name)
                except OSError as exc:
                    failure(path, exc)
        except OSError as exc:
            failure(directory, exc)
    if _component(item.name) and not installed._cancelled(cancel):
        for kind in ('common_start','user_start'):
            path = Path(bases[kind])/'Programs'/(item.name+'.lnk')
            if path.is_file() and os.path.normcase(str(path)) not in seen:
                try:
                    files.append(_file_snapshot(path, path.parent, _path_guard(path, other_roots)))
                except OSError as exc:
                    failure(path, exc)
    if registry is not None and not installed._cancelled(cancel):
        path = 'SOFTWARE\\'+leaf
        shared = any(Path(other.location).name.casefold() == leaf.casefold() or
            Path(other.source.get('executable', '')).parent.name.casefold() == leaf.casefold() for other in others)
        for hive in ('HKLM','HKCU'):
            for view in (32,64):
                try:
                    digest, children = _registry_snapshot(registry, hive, path, view)
                    reason = '同名软件仍已安装，保留共享配置' if shared else '包含子键，按非递归方式保留' if children else ''
                    keys.append(dict(root=hive,path=path,view=view,snapshot=digest,blocked=reason))
                    keys.extend(dict(root=hive,path=path+'\\'+child,view=view,blocked='仅展示，不递归删除')
                                for child in children)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    errors.append(f'{hive}\\{path}：{exc}')
    token = secrets.token_hex(24)
    with _LOCK:
        _MANIFESTS[token] = dict(item=item, files=copy.deepcopy(files), registry=copy.deepcopy(keys),
                                 other_roots=other_roots)
        while len(_MANIFESTS) > 32:
            _MANIFESTS.pop(next(iter(_MANIFESTS)))
    return dict(token=token, files=files, registry=keys, errors=errors, cancelled=installed._cancelled(cancel))


def clean_residue(manifest, cancel=None, progress=None, *, registry=None):
    registry = installed.winreg if registry is None else registry
    with _LOCK:
        original = _MANIFESTS.get(manifest.get('token'))
    if original is None:
        return dict(deleted=0, errors=['残留清单已过期，请重新扫描'], cancelled=False)
    deleted, errors = 0, []
    try:
        _completed(original['item'])
    except (OSError, ValueError) as exc:
        return dict(deleted=0, errors=[str(exc)], cancelled=False)
    for entry in manifest.get('files', []):
        if installed._cancelled(cancel):
            break
        if entry.get('blocked'):
            continue
        try:
            if entry not in original['files']:
                raise ValueError('文件不属于已扫描残留清单')
            path = Path(entry['path'])
            reason = _path_guard(path, original['other_roots'])
            if reason:
                raise ValueError(reason)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns] != entry['identity']:
                raise ValueError('文件已变化，已保留')
            if progress:
                progress({'message': '清理卸载残留文件'})
            try:
                path.unlink()
            except PermissionError:
                if getattr(info,'st_file_attributes',0) & 1:
                    path.chmod(info.st_mode | stat.S_IWRITE)
                    try:
                        path.unlink()
                    except OSError:
                        path.chmod(info.st_mode)
                        raise
                else:
                    raise
            deleted += 1
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    for entry in manifest.get('registry', []):
        if installed._cancelled(cancel):
            break
        if entry.get('blocked'):
            continue
        try:
            if entry not in original['registry'] or entry['root'] not in ('HKLM','HKCU'):
                raise ValueError('注册表键不属于已扫描残留清单')
            digest, children = _registry_snapshot(registry,entry['root'],entry['path'],entry['view'])
            if children or digest != entry['snapshot']:
                raise ValueError('注册表键已变化，已保留')
            hive = registry.HKEY_LOCAL_MACHINE if entry['root'] == 'HKLM' else registry.HKEY_CURRENT_USER
            view = registry.KEY_WOW64_32KEY if entry['view'] == 32 else registry.KEY_WOW64_64KEY
            registry.DeleteKeyEx(hive,entry['path'],view,0)
            deleted += 1
        except (OSError, ValueError, AttributeError) as exc:
            errors.append(str(exc))
    from cplus_cleaner.application import preferences
    try:
        preferences.append_activity('uninstall_residue', ok=not errors,
            summary=f"{original['item'].name}：清理{deleted}项残留，{len(errors)}项保留", count=deleted)
    except OSError:
        pass
    return dict(deleted=deleted, errors=errors, cancelled=installed._cancelled(cancel))

# Purpose: Supported post-uninstall residue checks
