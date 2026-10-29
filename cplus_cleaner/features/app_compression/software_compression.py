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
import re
import stat
import subprocess
import sys
import threading
import time
import uuid

from cplus_cleaner.common import core, runtime
from cplus_cleaner.application import preferences
from cplus_cleaner.features.uninstall import software_uninstall as inventory
from cplus_cleaner.platforms import software_windows as windows

WOF_TAG = 0x80000017
_CATALOG = {}
_LOCK = threading.RLock()
LARGE_FILE_BYTES = 32 * 1024 * 1024
UNCOMPRESSED_NOTICE = '部分文件未压缩，未计入节省空间'


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
    forbidden = list(map(core.canonical, runtime.protected_paths(core.APP_DIR)))
    if not runtime.frozen():
        forbidden.append(core.canonical(Path(sys.executable).parent))
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


def _mark_restored(record, physical):
    """Commit a verified completed restore without overwriting concurrent work."""
    with _LOCK:
        records = _records()
        current = next((row for row in records if row.get('id') == record['id']), None)
        if current != record:
            return False
        current.update(restored=True, remaining=0, pending=0, status='success',
                       current_size=physical, restored_time=int(time.time()), last_errors=[])
        _atomic(_directory()/'records.json', records)
    return True


def restore_started(record):
    """A partial compression is not an interrupted restore."""
    errors = record.get('last_errors') or []
    errors = [errors] if isinstance(errors, str) else errors
    return (record.get('operation') == 'restore' or 'restore_before' in record or 'restore_after' in record
            or any('部分文件仍处于压缩状态，已保留还原记录' in str(error)
                   for error in errors))


def record_view(record):
    """Drop the old synthetic failure without hiding real command errors."""
    row = copy.deepcopy(record)
    if restore_started(row):
        return row
    errors = row.get('last_errors') or []
    errors = [errors] if isinstance(errors, str) else errors
    cleaned, changed = [], False
    for error in errors:
        text = str(error)
        if UNCOMPRESSED_NOTICE not in text:
            cleaned.append(error)
            continue
        changed = True
        if re.search(r'\[ERR|error|denied|拒绝|拒絕|失败|失敗|错误|錯誤|无法|無法', text, re.I):
            cleaned.append(text.replace(UNCOMPRESSED_NOTICE, '').strip())
    if changed:
        row['last_errors'] = cleaned
        if not cleaned and row.get('status') in {'partial', 'failed', 'success'} and not row.get('pending'):
            row.update(status='success', operation='compress', skipped_uncompressed=True)
    return row


def _finished_without_payload(record):
    """Keep a completed all-skipped attempt until the user explicitly restores it."""
    return (record.get('status') == 'success' and not restore_started(record)
            and not record.get('restored') and not record.get('superseded')
            and not record.get('remaining') and not record.get('compressed_files')
            and bool(record.get('skipped_files') or record.get('skipped_uncompressed')))


def _refresh_record(record, *, measured=None, cancel=None):
    row = record_view(record)
    row['can_restore'] = not row.get('restored') and not row.get('superseded') and bool(
        row.get('remaining', row.get('processed') or row.get('pending')) or _finished_without_payload(row))
    if row.get('superseded') or _cancelled(cancel):
        return row
    try:
        snapshot = json.loads((_directory()/(_record_id(row['id'])+'.json')).read_text('utf-8'))
        if snapshot['folder'] != row['install_path']:
            raise ValueError('压缩记录目录不匹配')
        root = _validate_root(Path(snapshot['folder']), snapshot['root'])
        measured = _measure(root, cancel) if measured is None else measured
        if not _cancelled(cancel) and not measured['errors'] and not measured['blocked']:
            row['current_size'] = measured['physical']
            if not row.get('restored'):
                remaining, errors = _restore_remaining(root, snapshot['files'])
                row['can_restore'] = bool(remaining) or _finished_without_payload(row)
                if not remaining and not errors and not _finished_without_payload(row) and not _cancelled(cancel):
                    if _mark_restored(record, measured['physical']):
                        row.update(restored=True, remaining=0, pending=0, status='success', can_restore=False, last_errors=[])
                    else:
                        row['can_restore'] = True  # Re-read after concurrent work finishes.
        elif not _cancelled(cancel):
            row['refresh_error'] = '目录未完整读取，请重新扫描'
    except (OSError, ValueError, KeyError) as exc:
        row['refresh_error'] = str(exc)
        row['can_restore'] = False
    return row


def history(*, refresh=False, cancel=None):
    with _LOCK:
        records = _records()
    result = []
    for record in reversed(records):
        row = record_view(record)
        row['can_restore'] = not row.get('restored') and not row.get('superseded') and bool(
            row.get('remaining', row.get('processed') or row.get('pending')) or _finished_without_payload(row))
        if refresh:
            row = _refresh_record(record, cancel=cancel)
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
        active = [_refresh_record(record, measured=data, cancel=cancel) for record in active]
        if any(not record.get('restored') for record in active):
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
        # Managed attempts stay in history until restored. Native or newly
        # created compressed files must not hide a restored application.
        model = Software(item.key, item.name, str(root), data['physical'],
                         None, item.icon, blocked, False, len(data['files']))
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


def _checked_file(root, entry):
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
    if not stat.S_ISREG(info.st_mode) or core.is_link(path) or info.st_nlink > 1:
        raise ValueError('文件身份、大小或修改时间已变化，已保留')
    return path, info


def _validate_file(root, entry):
    path, info = _checked_file(root, entry)
    if _identity(info) != entry['identity']:
        raise ValueError('文件身份、大小或修改时间已变化，已保留')
    return path


def _restore_candidate(root, entry):
    try:
        path, info = _checked_file(root, entry)
    except FileNotFoundError:
        return None  # Missing files no longer need decompression.
    if not _file_compressed(path, info):
        return None  # Changed, already uncompressed files must not cause a false failure.
    # Restore current ordinary files at retained paths, including rotated logs.
    # The caller captures/revalidates their current identities before compact.
    return path


def _restore_remaining(root, entries):
    remaining, errors = 0, []
    for entry in entries:
        try:
            if _restore_candidate(root, entry) is not None:
                remaining += 1
        except (OSError, ValueError) as exc:
            remaining += 1  # Unreadable or unsafe entries remain retryable.
            if len(errors) < 50:
                errors.append(str(exc))
    return remaining, errors


class _IoCounters(ct.Structure):
    _fields_ = [(name, ct.c_ulonglong) for name in (
        'reads', 'writes', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]


@lru_cache(maxsize=1)
def _io_api():
    library = ct.WinDLL('kernel32', use_last_error=True)
    function = library.GetProcessIoCounters
    function.argtypes = [wt.HANDLE, ct.POINTER(_IoCounters)]
    function.restype = wt.BOOL
    return library, function


def _process_read_bytes(process):
    """Read I/O performed by our compact child; None means unavailable."""
    if os.name != 'nt' or getattr(process, '_handle', None) is None:
        return None
    try:
        counters = _IoCounters()
        return counters.read_bytes if _io_api()[1](int(process._handle), ct.byref(counters)) else None
    except (OSError, AttributeError, TypeError, ValueError):
        return None


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
            started = time.monotonic()
            sizes = [max(1,path.stat().st_size) for path in selected]
            total_bytes = sum(sizes)
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
                    if progress:
                        read_bytes = _process_read_bytes(process)
                        progress(dict(busy=True,elapsed=max(0,int(time.monotonic()-started)),
                            current_file=str(selected[0]),
                            current_file_size=sizes[0],
                            bytes_processed=min(read_bytes or 0,total_bytes*.99),bytes_total=total_bytes))
            code = max(code,process.returncode)
            text = output.decode('mbcs',errors='replace')
            lines = text.splitlines()
            failures = ['\n'.join(lines[index:index+3]) for index,line in enumerate(lines)
                        if re.search(r'\[ERR(?:OR)?\]', line, re.I)] if compress else []
            if failures:
                code = max(1, code)  # /I can report file errors with exit code 0.
                outputs.append('\n'.join(failures[-5:])[:1000])
            else:
                outputs.append(text[-3000:])
    skipped = 0
    if compress:
        # Windows may leave tiny or unsuitable files uncompressed. A completed
        # command need not compress every file, and these files are not retried.
        skipped = sum(not _file_compressed(path) for path in paths)
    else:
        remaining = [path for path in paths if _file_compressed(path)]
        if not remaining:
            code = 0
        else:
            code = max(1, code)
            outputs.append('仍处于压缩状态：'+'；'.join(str(path) for path in remaining[:5]))
    return dict(returncode=code, output='\n'.join(outputs), skipped_files=skipped)


def _batches(entries):
    batch, length = [], 0
    for entry in entries:
        if entry['identity'][2] >= LARGE_FILE_BYTES:
            if batch:
                yield batch
                batch, length = [], 0
            yield [entry]
            continue
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
    root = _validate_root(Path(item.folder), snapshot['root'])
    if _cancelled(cancel):
        return dict(status='cancelled',item=item,before=item.size,after=item.after,saved=0,
                    errors=[],cancelled=True,record=None)
    baseline = _measure(root, cancel)
    if baseline['errors'] or baseline['blocked']:
        raise ValueError('目录未完整读取，请重新扫描')
    if _cancelled(cancel):
        return dict(status='cancelled',item=item,before=baseline['physical'],after=baseline['physical'],
                    saved=0,errors=[],cancelled=True,record=None)
    before = baseline['physical']
    record = dict(id=uuid.uuid4().hex,name=item.name,install_path=str(root),old_size=before,
        new_size=before,time=int(time.time()),schema=2,logical_size=baseline['logical'],
        saved_bytes=0,restored=False,superseded=False,processed=0,status='running') if compressing else copy.deepcopy(previous)
    if record is None:
        record = dict(id=uuid.uuid4().hex,name=item.name,install_path=str(root),old_size=before,
            new_size=before,time=int(time.time()),
            restored=False,superseded=False,processed=0,status='running')
    record['operation'] = 'compress' if compressing else 'restore'
    journal = dict(name=item.name,key=item.key,folder=str(root),root=snapshot['root'],files=copy.deepcopy(snapshot['files']),
                   size=item.size,source=snapshot.get('source',{}))
    if compressing:
        record['pending'] = len(snapshot['files'])
    # Verify that a recovery record can be persisted before changing file attributes.
    _store(record, journal if compressing else snapshot)
    entries, errors, processed, examined = snapshot['files'], [], [], 0
    total_bytes = sum(max(1,entry['identity'][2]) for entry in entries)
    completed_bytes, batch_bytes = 0, 0
    def report(activity=None, *, finished=False):
        if progress:
            activity = activity or {}
            partial = min(batch_bytes*.99,max(0,activity.get('bytes_processed',0)))
            value = 100 if finished else min(99,100*(completed_bytes+partial)/max(1,total_bytes))
            progress(dict(activity,message=('压缩' if compressing else '还原')+'：'+item.name,
                completed=examined,total=len(entries),bytes_completed=completed_bytes+partial,
                bytes_total=total_bytes,progress=value,busy=bool(activity.get('busy'))))
    for batch in _batches(entries):
        if _cancelled(cancel):
            break
        batch_bytes = sum(max(1,entry['identity'][2]) for entry in batch)
        valid = []
        for entry in batch:
            try:
                path = _validate_file(root, entry) if compressing else _restore_candidate(root, entry)
                if path is not None:
                    if not compressing:
                        entry = dict(relative=entry['relative'], identity=_identity(path.lstat()))
                        path = _validate_file(root, entry)
                    valid.append((entry,path))
            except (OSError,ValueError) as exc:
                if len(errors)<50:errors.append(str(exc))
        if valid:
            report()
            try:
                paths = [path for entry,path in valid]
                outcome = (runner(paths,compressing) if runner is not None
                           else _compact(paths,compressing,cancel,report))
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
        examined += len(batch)
        completed_bytes += batch_bytes
        report()
    measured = _measure(root)
    errors.extend(measured['errors'])
    errors.extend(measured['blocked'])
    cancelled = _cancelled(cancel)
    status = 'cancelled' if cancelled else 'partial' if errors and processed else 'failed' if errors else 'success'
    remaining = None
    if not compressing:
        remaining, verification_errors = _restore_remaining(root, entries)
        errors.extend(error for error in verification_errors if error not in errors)
        if remaining and not cancelled:
            errors.append('部分文件仍处于压缩状态，已保留还原记录')
            status = 'partial' if processed else 'failed'
        elif not remaining and not cancelled and not measured['errors'] and not measured['blocked']:
            # A nonzero compact exit can describe a file already decompressed by
            # Windows. Verification of every retained entry is authoritative.
            status, errors = 'success', []
    # This UI flag also marks a completed managed attempt, including one where
    # every file was skipped, so it cannot be dispatched for compression again.
    model = replace(item,size=before if compressing else measured['physical'],
                    after=measured['physical'],
                    compressed=(measured['compressed'] or status == 'success') if compressing else bool(remaining))
    with _LOCK:
        if model.icon is None and item.key in _CATALOG:
            model = replace(model,icon=_CATALOG[item.key]['item'].icon)
    record.update(current_size=measured['physical'],processed=len(processed),status=status,
                  last_errors=[str(error)[:1000] for error in errors[:5]],
                  restored=not compressing and status=='success')
    if compressing:
        record.update(new_size=measured['physical'],saved_bytes=max(0,before-measured['physical']))
        record['pending'] = 0 if status=='success' else len(entries)-len(processed)
        if status == 'success':
            record['remaining'], _ = _restore_remaining(root, journal['files'])
            record.update(compressed_files=record['remaining'],
                          skipped_files=max(0,len(processed)-record['remaining']))
    else:
        record.update(remaining=remaining,restore_before=before,restore_after=measured['physical'])
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
            bytes_before=before,bytes_after=measured['physical'])
    except OSError:
        pass
    if not cancelled:
        examined = len(entries)
        completed_bytes = total_bytes
        report(finished=True)
    return dict(status=status,item=model,before=before,after=measured['physical'],
        saved=max(0,before-measured['physical']) if compressing else 0,
        errors=errors,cancelled=cancelled,record=record)


def compress(item, cancel=None, progress=None, *, runner=None):
    snapshot = _authorize(item)
    if item.compressed:
        raise ValueError('该软件已完成压缩，请先还原')
    return _operate(item,snapshot,True,cancel,progress,runner)


def restore(item_or_record, cancel=None, progress=None, *, runner=None):
    if isinstance(item_or_record, Software):
        item = item_or_record
        snapshot = _authorize(item)
        with _LOCK:
            previous = next((row for row in reversed(_records()) if row.get('install_path')==item.folder
                and not row.get('restored') and not row.get('superseded')),None)
        if previous is not None:
            return restore(previous,cancel,progress,runner=runner)
        return _operate(item,snapshot,False,cancel,progress,runner)
    identity = _record_id(item_or_record.get('id'))
    with _LOCK:
        record = next((row for row in _records() if row.get('id')==identity),None)
        # Counts describe the last attempt, not the recovery capability. A
        # failed attempt can touch zero files while compressed files remain.
        if record is None or record.get('restored') or record.get('superseded'):
            raise ValueError('该压缩记录已还原、已更新或不可用')
    try:
        snapshot = json.loads((_directory()/(identity+'.json')).read_text('utf-8'))
        if snapshot['folder'] != record['install_path']:
            raise ValueError('压缩记录目录不匹配')
        item = Software(snapshot['key'],record['name'],record['install_path'],record['old_size'],
                        record['new_size'],compressed=True,files=len(snapshot['files']))
        return _operate(item,snapshot,False,cancel,progress,runner,record)
    except (OSError, ValueError, KeyError) as exc:
        # Preflight failures happen before _operate can save an outcome. Keep
        # their reason beside the recovery entry instead of leaving a silent retry.
        record = dict(record, status='failed', operation='restore', last_errors=[str(exc)[:1000]])
        try:
            _store(record)
        except OSError:
            pass
        raise

# Purpose: Transparent compression, restore, eligibility and history
