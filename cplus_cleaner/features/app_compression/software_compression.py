"""Installed-software transparent compression, with retained file snapshots.

Original: fq_GetSoftCompactList -> LocalSoftEnum(mode1), NTFS filtering;
fq_SoftCompact -> Windows compact /C or /U /I /F /EXE:LZX.
This implementation operates on an explicit scanned file list, never /S roots.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import copy
import ctypes as ct
from ctypes import wintypes as wt
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
import uuid

from cplus_cleaner.common import core
from cplus_cleaner.application import preferences
from cplus_cleaner.features.uninstall import software_uninstall as inventory
from cplus_cleaner.platforms import software_windows as windows

WOF_TAG = 0x80000017
_CATALOG = {}
_LOCK = threading.RLock()


@dataclass(frozen=True)
class Software:
    key: str
    name: str
    folder: str
    size: int
    after: int | None = None
    icon: bytes | None = None
    blocked: str = ''
    compressed: bool = False
    files: int = 0


def _cancelled(cancel):
    return cancel is not None and cancel.is_set()


def allocated_size(path):
    """Physical file allocation, including WOF executable compression."""
    if os.name != 'nt':
        return path.stat().st_size
    kernel = ct.WinDLL('kernel32', use_last_error=True)
    kernel.GetCompressedFileSizeW.argtypes = [wt.LPCWSTR, ct.POINTER(wt.DWORD)]
    kernel.GetCompressedFileSizeW.restype = wt.DWORD
    high = wt.DWORD()
    ct.set_last_error(0)
    low = kernel.GetCompressedFileSizeW(str(path), ct.byref(high))
    if low == 0xffffffff and ct.get_last_error():
        raise ct.WinError(ct.get_last_error())
    return (high.value << 32) | low


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def _compressed(info):
    return bool(getattr(info, 'st_file_attributes', 0) & 0x800 or
                getattr(info, 'st_reparse_tag', 0) == WOF_TAG)


@lru_cache(maxsize=1)
def _wof_api():
    if os.name != 'nt':return None
    try:
        library = ct.WinDLL(str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/Wofutil.dll'))
        function = library.WofIsExternalFile
        function.argtypes = [wt.LPCWSTR,ct.POINTER(wt.BOOL),ct.POINTER(wt.ULONG),ct.c_void_p,ct.c_void_p]
        function.restype = ct.c_long
        return library,function
    except (OSError,AttributeError):
        return None


def _wof_file(path):
    api = _wof_api()
    if api is None:return False
    external,provider = wt.BOOL(),wt.ULONG()
    result = api[1](str(path),ct.byref(external),ct.byref(provider),None,None)
    return result == 0 and bool(external.value) and provider.value == 2


def _file_compressed(path, info=None):
    return _compressed(info or path.lstat()) or _wof_file(path)


def _guard(root):
    root = core.canonical(Path(root))
    if not root.is_dir() or not root.is_absolute():
        raise ValueError('软件安装目录不存在')
    shared = [Path(root.anchor), core.canonical(Path.home())]
    forbidden = [core.canonical(core.APP_DIR), core.canonical(Path(sys.executable).parent)]
    for name in ('SystemRoot', 'ProgramData'):
        if os.environ.get(name):
            forbidden.append(core.canonical(Path(os.environ[name])))
    for name in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA', 'APPDATA'):
        if os.environ.get(name):
            shared.append(core.canonical(Path(os.environ[name])))
    for name in ('ProgramFiles', 'ProgramFiles(x86)'):
        if os.environ.get(name):
            forbidden.extend(core.canonical(Path(os.environ[name])/child)
                             for child in ('WindowsApps', 'Common Files'))
    if root in shared or any(core.inside(root, base) or core.inside(base, root) for base in forbidden):
        raise ValueError('系统、共享组件或当前运行程序目录受保护')
    for current in (root, *root.parents):
        if current.exists() and core.is_link(current):
            raise ValueError('软件路径包含链接或重解析目录')
    if windows.filesystem(root).upper() != 'NTFS':
        raise ValueError('软件压缩仅支持 NTFS 分区')
    return root


def _measure(root, cancel=None):
    root = core.canonical(Path(root))
    entries, errors, blocked, logical, physical, compressed = [], [], [], 0, 0, False
    stack = [root]
    while stack and not _cancelled(cancel):
        current = stack.pop()
        try:
            if core.is_link(current):
                blocked.append('目录内包含链接或重解析项')
                continue
            with os.scandir(current) as children:
                for child in children:
                    if _cancelled(cancel):
                        break
                    path = Path(child.path)
                    try:
                        # Windows DirEntry.stat may report zero file identities;
                        # use the same handle-backed lstat as operation checks.
                        info = path.lstat()
                        wof = getattr(info, 'st_reparse_tag', 0) == WOF_TAG
                        if stat.S_ISLNK(info.st_mode) or (getattr(info, 'st_file_attributes', 0) & 0x400 and not wof):
                            blocked.append('目录内包含链接或重解析项')
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            stack.append(path)
                        elif stat.S_ISREG(info.st_mode):
                            logical += info.st_size
                            physical += allocated_size(path)
                            compressed = compressed or _file_compressed(path,info)
                            if info.st_nlink > 1:
                                blocked.append('目录内包含共享硬链接文件')
                            entries.append(dict(relative=path.relative_to(root).as_posix(), identity=_identity(info)))
                    except OSError as exc:
                        if len(errors) < 30:
                            errors.append(f'{path}：{exc}')
        except OSError as exc:
            if len(errors) < 30:
                errors.append(f'{current}：{exc}')
    return dict(files=entries, logical=logical, physical=physical, compressed=compressed,
                errors=errors, blocked=list(dict.fromkeys(blocked)))


def _directory():
    return core.DATA_DIR/'compression'


def _records():
    try:
        value = json.loads((_directory()/'records.json').read_text('utf-8'))
    except FileNotFoundError:
        return []
    if not isinstance(value, list):
        raise ValueError('压缩记录格式无效')
    return value


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def history():
    with _LOCK:
        records = _records()
    result = []
    for record in reversed(records):
        row = copy.deepcopy(record)
        row['can_restore'] = not row.get('restored') and not row.get('superseded') and bool(row.get('processed') or row.get('pending'))
        result.append(row)
    return result


def _record_id(value):
    if not isinstance(value, str) or len(value) != 32 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('压缩记录标识无效')
    return value


def scan(cancel=None, progress=None, *, records=None, icons=True):
    measured = {}
    def measure(root, token, errors):
        data = _measure(root, token)
        measured[os.path.normcase(str(root))] = data
        errors.extend(data['errors'][:max(0,100-len(errors))])
        return data['logical']//1024*1024
    raw = inventory.scan(cancel, progress, records=records, icons=icons,
                         retain_inventory=False, size_reader=measure)
    try:
        saved = _records()
    except (OSError, ValueError) as exc:
        saved = []
        raw['errors'].append(str(exc))
    result, issued = [], {}
    skipped = raw['skipped']
    system_root = core.canonical(Path(os.environ.get('SystemRoot', r'C:\Windows')))
    for item in raw['items']:
        root = core.canonical(Path(item.location))
        data = measured[os.path.normcase(str(root))]
        if not data['files'] or str(system_root).casefold() in str(root).casefold():
            skipped += 1
            continue
        try:
            if windows.filesystem(root).upper() != 'NTFS':
                skipped += 1
                continue
        except OSError as exc:
            raw['errors'].append(f'{item.name}：{exc}')
            continue
        active = [record for record in saved if not record.get('restored') and not record.get('superseded')
                  and os.path.normcase(record.get('install_path','')) == os.path.normcase(str(root))]
        if active and data['compressed']:
            continue  # Completed applications remain available in compression history.
        blocked = ''
        try:
            _guard(root)
        except (OSError, ValueError) as exc:
            blocked = str(exc)
        if not blocked and data['blocked']:
            blocked = '；'.join(data['blocked'])
        if not blocked and data['errors']:
            blocked = '目录未完整读取，请重新扫描'
        program_roots = [Path(os.environ[name]) for name in ('ProgramFiles','ProgramFiles(x86)') if os.environ.get(name)]
        if not blocked and not windows.administrator() and any(core.inside(root, core.canonical(base)) for base in program_roots):
            blocked = '需要以管理员身份运行后操作'
        if blocked:
            skipped += 1
            continue
        model = Software(item.key, item.name, str(root), item.size,
                         data['physical'] if data['compressed'] else None, item.icon, blocked,
                         data['compressed'], len(data['files']))
        info = root.stat()
        result.append(model)
        issued[model.key] = dict(item=model, root=[info.st_dev,info.st_ino],
            source=copy.deepcopy(item.source), files=copy.deepcopy(data['files']))
    with _LOCK:
        _CATALOG.clear()
        _CATALOG.update(issued)
    result.sort(key=lambda item: item.size, reverse=True)
    return dict(items=result, errors=raw['errors'], skipped=skipped, cancelled=raw['cancelled'])


def _authorize(item):
    with _LOCK:
        snapshot = copy.deepcopy(_CATALOG.get(item.key))
    if snapshot is None or snapshot['item'] != item:
        raise ValueError('软件不属于本次扫描，请重新扫描')
    if item.blocked:
        raise ValueError(item.blocked)
    source = snapshot['source']
    if inventory._read_key(inventory.winreg, source) != source['snapshot']:
        raise ValueError('软件安装信息已变化，请重新扫描')
    return snapshot


def _validate_root(root, identity):
    root = _guard(root)
    info = root.stat()
    if [info.st_dev,info.st_ino] != identity:
        raise ValueError('软件目录已被替换，请重新扫描')
    return root


def _validate_file(root, entry):
    relative = Path(entry['relative'])
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('文件超出压缩清单范围')
    path = core.canonical(root/relative)
    if not core.inside(path, root):
        raise ValueError('文件超出软件目录')
    for parent in path.parents:
        if not core.inside(parent, root):
            break
        if core.is_link(parent):
            raise ValueError('文件父目录已变成链接')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or core.is_link(path) or info.st_nlink > 1 or _identity(info) != entry['identity']:
        raise ValueError('文件身份、大小或修改时间已变化，已保留')
    return path


def _compact(paths, compress, cancel=None, progress=None):
    executable = Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/compact.exe'
    if os.name != 'nt' or not executable.is_file():
        raise OSError('Windows compact 不可用')
    base = [str(executable), '/C' if compress else '/U', '/I', '/F', '/Q']
    modern = sys.getwindowsversion().major >= 10
    variants = [(base+(['/EXE:LZX'] if modern else []),paths)] if compress else [
        (base+['/EXE:LZX'],[path for path in paths if getattr(path.lstat(),'st_reparse_tag',0)==WOF_TAG or _wof_file(path)]),
        (base,[path for path in paths if getattr(path.lstat(),'st_file_attributes',0)&0x800])]
    outputs, code = [], 0
    for arguments, selected in variants:
        if not selected or _cancelled(cancel):
            continue
        with subprocess.Popen(arguments+[str(path) for path in selected],stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,creationflags=0x08000000) as process:
            while True:
                try:
                    output,_ = process.communicate(timeout=.2)
                    break
                except subprocess.TimeoutExpired:
                    if _cancelled(cancel):
                        # This is our compact process, never the installed app.
                        process.terminate()
                        output,_ = process.communicate()
                        break
            code = max(code,process.returncode)
            outputs.append(output.decode('mbcs',errors='replace')[-3000:])
    if not compress and not any(_file_compressed(path) for path in paths):
        code=0
    return dict(returncode=code, output='\n'.join(outputs))


def _batches(entries):
    batch, length = [], 0
    for entry in entries:
        cost = len(entry['relative'])+320
        if batch and (len(batch)>=64 or length+cost>18000):
            yield batch
            batch, length = [], 0
        batch.append(entry);length+=cost
    if batch:
        yield batch


def _store(record, manifest=None):
    with _LOCK:
        records = _records()
        if manifest is not None:
            _atomic(_directory()/(_record_id(record['id'])+'.json'), manifest)
        for current in records:
            if current['id'] == record['id']:
                current.update(record)
                break
        else:
            for current in records:
                if not current.get('restored') and current.get('install_path') == record['install_path']:
                    current['superseded'] = True
            records.append(copy.deepcopy(record))
        _atomic(_directory()/'records.json', records)


def _operate(item, snapshot, compressing, cancel, progress, runner=None, previous=None):
    if runner is None:
        runner = lambda paths, mode: _compact(paths,mode,cancel,progress)
    root = _validate_root(Path(item.folder), snapshot['root'])
    if _cancelled(cancel):
        return dict(status='cancelled',item=item,before=item.size,after=item.after,saved=0,
                    errors=[],cancelled=True,record=None)
    record = dict(id=uuid.uuid4().hex,name=item.name,install_path=str(root),old_size=item.size,
        new_size=item.after if item.after is not None else item.size,time=int(time.time()),
        restored=False,superseded=False,processed=0,status='running') if compressing else copy.deepcopy(previous)
    if record is None:
        record = dict(id=uuid.uuid4().hex,name=item.name,install_path=str(root),old_size=item.size,
            new_size=item.after if item.after is not None else item.size,time=int(time.time()),
            restored=False,superseded=False,processed=0,status='running')
    journal = dict(name=item.name,key=item.key,folder=str(root),root=snapshot['root'],files=copy.deepcopy(snapshot['files']),
                   size=item.size,source=snapshot.get('source',{}))
    if compressing:
        record['pending'] = len(snapshot['files'])
    # Verify that a recovery record can be persisted before changing file attributes.
    _store(record, journal if compressing else snapshot)
    entries, errors, processed = snapshot['files'], [], []
    for batch in _batches(entries):
        if _cancelled(cancel):
            break
        valid = []
        for entry in batch:
            try:
                path = _validate_file(root, entry)
                valid.append((entry,path))
            except (OSError,ValueError) as exc:
                if len(errors)<50:errors.append(str(exc))
        if valid:
            if progress:
                progress(dict(message=('压缩' if compressing else '还原')+'：'+item.name,
                    completed=len(processed),total=len(entries),progress=int(100*len(processed)/max(1,len(entries)))))
            try:
                outcome = runner([path for entry,path in valid], compressing)
                if outcome.get('returncode'):
                    errors.append(outcome.get('output','Windows compact 返回失败')[-2000:])
            except OSError as exc:
                errors.append(str(exc))
                break
            for entry,path in valid:
                try:
                    _validate_file(root,entry)
                    processed.append(dict(relative=entry['relative'],identity=_identity(path.lstat())))
                except (OSError,ValueError) as exc:
                    if len(errors)<50:errors.append(str(exc))
            updates = {entry['relative']:entry for entry in processed}
            journal['files'] = [updates.get(entry['relative'],entry) for entry in snapshot['files']]
            record['processed'] = len(processed)
            if compressing:record['pending'] = len(entries)-len(processed)
            record['status'] = 'partial'
            _store(record)
        if progress:
            progress(dict(message=('压缩' if compressing else '还原')+'：'+item.name,
                completed=len(processed),total=len(entries),progress=int(100*len(processed)/max(1,len(entries)))))
    measured = _measure(root)
    errors.extend(measured['errors'])
    cancelled = _cancelled(cancel)
    status = 'cancelled' if cancelled else 'partial' if errors and processed else 'failed' if errors else 'success'
    if not compressing and not errors and any(_file_compressed(root/entry['relative']) for entry in processed):
        errors.append('部分文件仍处于压缩状态，已保留还原记录')
        status='partial'
    model = replace(item,after=measured['physical'],compressed=measured['compressed'])
    with _LOCK:
        if model.icon is None and item.key in _CATALOG:
            model = replace(model,icon=_CATALOG[item.key]['item'].icon)
    record.update(new_size=measured['physical'],processed=len(processed),status=status,
                  restored=not compressing and status=='success')
    if compressing:record['pending'] = 0 if status=='success' else len(entries)-len(processed)
    if record['restored']:record['restored_time']=int(time.time())
    _store(record,journal if compressing else snapshot)
    with _LOCK:
        if item.key in _CATALOG:
            _CATALOG[item.key]['item'] = model
            # Measurement may see files created since scan. They contribute to
            # display totals but never expand this operation's file capability.
            updates = {entry['relative']:entry for entry in processed}
            _CATALOG[item.key]['files'] = [updates.get(entry['relative'],entry) for entry in snapshot['files']]
    try:
        preferences.append_activity('software_compress' if compressing else 'software_restore',
            ok=status=='success',summary=f"{item.name}：{status}",count=len(processed),
            bytes_before=item.size,bytes_after=measured['physical'])
    except OSError:
        pass
    return dict(status=status,item=model,before=item.size,after=measured['physical'],
        saved=max(0,item.size-measured['physical']),errors=errors,cancelled=cancelled,record=record)


def compress(item, cancel=None, progress=None, *, runner=None):
    return _operate(item,_authorize(item),True,cancel,progress,runner)


def restore(item_or_record, cancel=None, progress=None, *, runner=None):
    if isinstance(item_or_record, Software):
        item = item_or_record
        snapshot = _authorize(item)
        with _LOCK:
            previous = next((row for row in reversed(_records()) if row.get('install_path')==item.folder
                and not row.get('restored') and not row.get('superseded') and (row.get('processed') or row.get('pending'))),None)
        if previous is not None:
            return restore(previous,cancel,progress,runner=runner)
        return _operate(item,snapshot,False,cancel,progress,runner)
    identity = _record_id(item_or_record.get('id'))
    with _LOCK:
        record = next((row for row in _records() if row.get('id')==identity),None)
        if record is None or record.get('restored') or record.get('superseded') or not (record.get('processed') or record.get('pending')):
            raise ValueError('该压缩记录已还原、已更新或不可用')
        snapshot = json.loads((_directory()/(identity+'.json')).read_text('utf-8'))
    if snapshot['folder'] != record['install_path']:
        raise ValueError('压缩记录目录不匹配')
    item = Software(snapshot['key'],record['name'],record['install_path'],record['old_size'],
                    record['new_size'],compressed=True,files=len(snapshot['files']))
    return _operate(item,snapshot,False,cancel,progress,runner,record)
