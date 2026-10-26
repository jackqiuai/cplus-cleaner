"""Hidden read-only DISM analysis, with output polling and optional Windows UAC."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from cplus_cleaner.common import core
from cplus_cleaner.common.runtime import launch_command


def emit_output(data, progress, last):
    if not progress or not data:
        return last
    # Percentages are ASCII in both OEM and UTF-16 DISM output.
    text = data.replace(b'\0', b'').decode('ascii', errors='ignore')
    values = re.findall(r'(\d{1,3}(?:\.\d+)?)\s*%', text)
    if values:
        percent = min(100.0, max(0.0, float(values[-1])))
        if last is None or percent >= last:
            if percent != last:
                progress(dict(percent=percent))
            return percent
    return last


def _tail(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b''


def run_streamed(command, progress):
    """Separate reader handles avoid pipe deadlock and shared seek offsets."""
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dism-output-', dir=core.DATA_DIR) as directory:
        output = Path(directory) / 'stdout.bin'
        with output.open('xb') as stream:
            process = subprocess.Popen(command, shell=False, stdout=stream, stderr=subprocess.STDOUT,
                                       creationflags=0x08000000)
            try:
                last = None
                while process.poll() is None:
                    last = emit_output(_tail(output), progress, last)
                    time.sleep(.15)
                data = _tail(output)
                emit_output(data, progress, last)
                return subprocess.CompletedProcess(command, process.returncode, data, b'')
            finally:
                # Do not interrupt Windows servicing if display/output handling fails.
                process.wait()


def run_elevated(progress):
    """Elevate only the fixed read-only analysis helper, never the main app."""
    from cplus_cleaner.features.uninstall.uninstall_windows import _elevated, _kernel
    from cplus_cleaner.features.system.cdrive_system import _decode_output
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='component-analysis-', dir=core.DATA_DIR) as directory:
        job = Path(directory).resolve()
        executable, *arguments = launch_command('--component-analysis-job', job)
        if progress:
            progress('等待 Windows 管理员授权…')
        try:
            process = _elevated(_kernel(), Path(executable), subprocess.list2cmdline(arguments), show=0)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1223:
                return dict(ok=False, cancelled=True, message='已取消管理员授权，组件分析未执行。', output='')
            raise
        try:
            last, started = None, False
            while True:
                waited = process.kernel.WaitForSingleObject(process.handle, 150)
                if waited == 0xffffffff:
                    raise OSError('无法等待组件分析进程')
                if waited == 0:
                    break
                data = _tail(job / 'stdout.bin')
                if (job / 'stdout.bin').exists() and not started:
                    started = True
                    if progress:
                        progress('正在分析 Windows 组件存储…')
                last = emit_output(data, progress, last)
            data = _tail(job / 'stdout.bin')
            emit_output(data, progress, last)
            try:
                result = json.loads((job / 'result.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                result = dict(ok=False, message='组件分析未返回结果，请重试。')
            result.update(stdout=_decode_output(data), stderr='', output=_decode_output(data))
            return result
        finally:
            process.close()


def helper_main(directory):
    """The helper accepts an empty job folder, not a command or action argument."""
    from cplus_cleaner.features.system import cdrive_system
    job = Path(directory)
    if not job.is_absolute() or not job.is_dir() or not job.name.startswith('component-analysis-'):
        raise ValueError('Invalid component analysis job')
    if job.parent.name.casefold() != 'ccleanner' or any(core.is_link(part) for part in (job, *job.parents)):
        raise ValueError('Invalid component analysis location')
    if any(job.iterdir()):
        raise ValueError('Component analysis job must be empty')
    try:
        if not cdrive_system._is_admin():
            raise PermissionError('组件分析需要管理员权限。')
        command = [str(Path(cdrive_system._system_directory()) / 'Dism.exe'),
                   '/Online', '/Cleanup-Image', '/AnalyzeComponentStore']
        with (job / 'stdout.bin').open('xb') as stream:
            completed = subprocess.run(command, shell=False, stdout=stream, stderr=subprocess.STDOUT,
                                       creationflags=0x08000000)
        success = completed.returncode in (0, 3010)
        result = dict(ok=success, returncode=completed.returncode, command=command,
                      message='组件分析已完成。' if success else f'组件分析失败，退出码 {completed.returncode}')
        if completed.returncode == 3010:
            result.update(restart_required=True, message='组件分析已完成，需要重启 Windows。')
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result = dict(ok=False, message=str(error))
    with (job / 'result.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False)


def report_text(output):
    """Human-readable report without console progress bars or terminal controls."""
    lines = []
    for raw in str(output or '').replace('\r', '\n').splitlines():
        line = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', raw).strip()
        if line and not re.fullmatch(r'[\[\] =.\d%]+', line):
            lines.append(line)
    return '\n'.join(lines)

# Purpose: Component analysis, progress and elevated read-only jobs
