"""Run a registered publisher command and wait for its Windows process job."""
from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
import os
from pathlib import Path
import subprocess
import time


def command_parts(command, executable):
    """Keep publisher arguments, including additional quoted parameters."""
    text = command.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end < 0:
            raise ValueError('卸载命令引号不完整')
        arguments = text[end+1:].lstrip()
    elif 'msiexec.' in text.lower():
        arguments = text.partition(' ')[2].lstrip()
    else:
        arguments = ''  # The inventory only admits an existing whole path here.
    return subprocess.list2cmdline([str(executable)]) + (' '+arguments if arguments else ''), arguments


class Process:
    def __init__(self, kernel, handle, job=None):
        self.kernel, self.handle, self.job = kernel, handle, job

    def wait(self, cancel=None, progress=None):
        class Accounting(ct.Structure):
            _fields_ = [(name, ct.c_longlong) for name in ('user','kernel','period_user','period_kernel')] + [
                (name, wt.DWORD) for name in ('faults','total','active','terminated')]
        notified = False
        while True:
            waited = self.kernel.WaitForSingleObject(self.handle, 200)
            if waited == 0xffffffff:
                raise ct.WinError(ct.get_last_error())
            active = False
            if self.job:
                accounting = Accounting()
                if not self.kernel.QueryInformationJobObject(self.job, 1, ct.byref(accounting), ct.sizeof(accounting), None):
                    raise ct.WinError(ct.get_last_error())
                active = bool(accounting.active)
            if waited == 0 and not active:
                break
            if waited == 0:
                time.sleep(.2)
            if cancel is not None and cancel.is_set() and not notified:
                notified = True
                if progress:
                    progress({'message': '已取消后续卸载，等待当前发布者程序结束…'})
        code = wt.DWORD()
        if not self.kernel.GetExitCodeProcess(self.handle, ct.byref(code)):
            raise ct.WinError(ct.get_last_error())
        return code.value

    def close(self):
        for attribute in ('handle', 'job'):
            handle = getattr(self, attribute)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, attribute, None)


def _kernel():
    kernel = ct.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ct.c_void_p, wt.LPCWSTR]
    kernel.CreateJobObjectW.restype = wt.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    kernel.QueryInformationJobObject.argtypes = [wt.HANDLE, ct.c_int, ct.c_void_p, wt.DWORD, ct.c_void_p]
    kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel.WaitForSingleObject.restype = wt.DWORD
    kernel.GetExitCodeProcess.argtypes = [wt.HANDLE, ct.POINTER(wt.DWORD)]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    kernel.ResumeThread.argtypes = [wt.HANDLE]
    kernel.ResumeThread.restype = wt.DWORD
    return kernel


def _elevated(kernel, executable, arguments, *, show=1):
    class ExecuteInfo(ct.Structure):
        _fields_ = [('size',wt.DWORD),('mask',wt.ULONG),('window',wt.HWND),
            ('verb',wt.LPCWSTR),('file',wt.LPCWSTR),('parameters',wt.LPCWSTR),
            ('directory',wt.LPCWSTR),('show',ct.c_int),('instance',wt.HINSTANCE),
            ('idlist',ct.c_void_p),('class_name',wt.LPCWSTR),('class_key',wt.HKEY),
            ('hotkey',wt.DWORD),('icon',wt.HANDLE),('process',wt.HANDLE)]
    shell = ct.WinDLL('shell32', use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ct.POINTER(ExecuteInfo)]
    info = ExecuteInfo()
    info.size, info.mask, info.verb = ct.sizeof(info), 0x40 | 0x100, 'runas'
    info.file, info.parameters, info.directory, info.show = str(executable), arguments, str(executable.parent), show
    if not shell.ShellExecuteExW(ct.byref(info)):
        raise ct.WinError(ct.get_last_error())
    if not info.process:
        raise OSError('发布者程序未返回可等待的进程句柄，请重新扫描核实')
    return Process(kernel, info.process)


def launch(command, executable):
    if os.name != 'nt':
        raise OSError('卸载程序仅支持Windows')
    executable = Path(executable)
    if executable.suffix.lower() != '.exe' or not executable.is_absolute():
        raise ValueError('卸载命令没有有效的绝对EXE路径')
    line, arguments = command_parts(command, executable)
    class StartupInfo(ct.Structure):
        _fields_ = [('size',wt.DWORD),('reserved',wt.LPWSTR),('desktop',wt.LPWSTR),('title',wt.LPWSTR)] + [
            (name,wt.DWORD) for name in ('x','y','width','height','cols','rows','fill','flags')] + [
            ('show',wt.WORD),('reserved_size',wt.WORD),('reserved_bytes',ct.c_void_p),
            ('input',wt.HANDLE),('output',wt.HANDLE),('error',wt.HANDLE)]
    class ProcessInfo(ct.Structure):
        _fields_ = [('process',wt.HANDLE),('thread',wt.HANDLE),('pid',wt.DWORD),('tid',wt.DWORD)]
    kernel = _kernel()
    kernel.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ct.c_void_p, ct.c_void_p,
        wt.BOOL, wt.DWORD, ct.c_void_p, wt.LPCWSTR, ct.POINTER(StartupInfo), ct.POINTER(ProcessInfo)]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ct.WinError(ct.get_last_error())
    startup, process = StartupInfo(), ProcessInfo()
    startup.size = ct.sizeof(startup)
    resumed = False
    try:
        if not kernel.CreateProcessW(str(executable), ct.create_unicode_buffer(line), None, None,
                False, 4, None, str(executable.parent), ct.byref(startup), ct.byref(process)):
            code = ct.get_last_error()
            if code == 740:
                return _elevated(kernel, executable, arguments)
            raise ct.WinError(code)
        if not kernel.AssignProcessToJobObject(job, process.process):
            raise ct.WinError(ct.get_last_error())
        if kernel.ResumeThread(process.thread) == 0xffffffff:
            raise ct.WinError(ct.get_last_error())
        resumed = True
        result = Process(kernel, process.process, job)
        process.process, job = None, None
        return result
    finally:
        # Never terminate a running publisher. Only dispose a suspended process
        # that could not be assigned to the monitoring job before it started.
        if process.process and not resumed:
            kernel.TerminateProcess(process.process, 1)
        for handle in (process.thread, process.process, job):
            if handle:
                kernel.CloseHandle(handle)
