"""Explicit Windows maintenance actions used by System Slim and paging shortcuts.

These system-owned files are never passed to the generic file deletion engine.
Importing or reading this module does not invoke a maintenance action.
"""
from __future__ import annotations

import codecs
import ctypes
from ctypes import wintypes
import json
import ntpath
import os
import stat
import subprocess
import time

from cplus_cleaner.common import core


CREATE_NO_WINDOW = 0x08000000
ADMIN_ACTIONS = frozenset({"hibernate_off", "hibernate_on", "analyze_component_store",
                           "cleanup_component_store"})
ACTION_LABELS = {
    "empty_recycle_bin": "永久清空系统盘回收站",
    "hibernate_off": "关闭休眠并释放休眠文件",
    "hibernate_on": "启用休眠",
    "analyze_component_store": "分析 Windows 组件存储",
    "cleanup_component_store": "清理 Windows 已替代组件",
    "pagefile_settings": "打开分页文件设置",
}


def _library(name):
    if os.name != "nt":
        raise OSError("此功能仅支持 Windows")
    return ctypes.WinDLL(name, use_last_error=True)


def _windows_path(api_name: str) -> str:
    kernel = _library("kernel32")
    function = getattr(kernel, api_name)
    function.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    function.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.value


def _windows_directory() -> str:
    return _windows_path("GetWindowsDirectoryW")


def _system_directory() -> str:
    return _windows_path("GetSystemDirectoryW")


def _system_drive() -> str:
    drive = ntpath.splitdrive(_windows_directory())[0]
    if len(drive) != 2 or drive[1] != ":":
        raise OSError("无法确定 Windows 系统盘")
    return drive + "\\"


def _is_admin() -> bool:
    shell = _library("shell32")
    shell.IsUserAnAdmin.argtypes = []
    shell.IsUserAnAdmin.restype = wintypes.BOOL
    return bool(shell.IsUserAnAdmin())


class _RecycleInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("i64Size", ctypes.c_int64),
                ("i64NumItems", ctypes.c_int64)]


def _query_recycle_bin(root: str) -> dict:
    shell = _library("shell32")
    function = shell.SHQueryRecycleBinW
    function.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_RecycleInfo)]
    function.restype = ctypes.c_int32
    info = _RecycleInfo()
    info.cbSize = ctypes.sizeof(info)
    code = function(root, ctypes.byref(info))
    if code < 0:
        raise OSError(f"查询回收站失败（HRESULT 0x{code & 0xffffffff:08X}）")
    return {"size": int(info.i64Size), "count": int(info.i64NumItems)}


def _empty_recycle_bin(root: str) -> int:
    shell = _library("shell32")
    function = shell.SHEmptyRecycleBinW
    function.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.DWORD]
    function.restype = ctypes.c_int32
    # Explicit UI action already describes the permanent deletion. Limit to one drive.
    return int(function(None, root, 0x1 | 0x2 | 0x4))


def _file_item(item_id, name, path, action, actions):
    item = dict(id=item_id, name=name, path=path, size=None, status="", action=action,
                actions=list(actions))
    try:
        info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            item["status"] = "不是普通文件；不提供直接删除"
        else:
            item.update(size=info.st_size, status="系统管理文件；通过下列系统功能管理")
            if item_id == "pagefile":
                item["status"] = "当前系统盘分页文件实际占用；不含其他盘分页文件和 swapfile.sys；由 Windows 管理"
    except FileNotFoundError:
        item.update(size=0, status="文件不存在或对应功能未启用")
    except OSError as exc:
        item["status"] = f"无法读取体积：{exc}"
    return item


def read_system_items() -> list[dict]:
    """Read sizes and states without changing system files or settings.

    ``size=None`` means unknown, never a measured zero. ``actions`` contains the
    explicit action IDs a UI may offer; ``action`` explains their effect.
    """
    descriptions = [
        ("hibernation", "系统休眠文件", "hiberfil.sys",
         "关闭休眠会释放此文件，也会关闭休眠及依赖休眠的快速启动；可重新启用。",
         ["hibernate_off", "hibernate_on"]),
        ("pagefile", "系统虚拟内存", "pagefile.sys",
         "由 Windows 管理。打开高级系统设置 → 性能设置 → 高级 → 虚拟内存；本程序不直接删除或改配置。",
         ["pagefile_settings"]),
        ("swapfile", "应用交换文件", "swapfile.sys",
         "由 Windows 管理，不直接删除；通过 Windows 虚拟内存设置管理。",
         ["pagefile_settings"]),
    ]
    try:
        root = _system_drive()
        windows = _windows_directory()
    except OSError as exc:
        items = [dict(id=key, name=name, path="", size=None, status=str(exc),
                      action=description, actions=[])
                 for key, name, _, description, _ in descriptions]
        items.extend(dict(id=key, name=name, path="", size=None, status=str(exc),
                          action="需要 Windows 系统 API", actions=[])
                     for key, name in [("recycle_bin", "系统盘回收站"),
                                       ("component_store", "Windows 更新与组件存储")])
        return items
    items = [_file_item(key, name, ntpath.join(root, filename), description, actions)
             for key, name, filename, description, actions in descriptions]
    recycle = dict(id="recycle_bin", name="系统盘回收站", path=ntpath.join(root, "$Recycle.Bin"),
                   size=None, status="", action="永久清空当前用户在系统盘的回收站文件。",
                   actions=["empty_recycle_bin"])
    try:
        recycle.update(_query_recycle_bin(root))
        recycle["status"] = f"当前用户在 {root} 的回收站：{recycle['count']:,} 个项目"
    except OSError as exc:
        recycle["status"] = str(exc)
    items.append(recycle)
    items.append(dict(id="component_store", name="Windows 更新与组件存储",
                      path=ntpath.join(windows, "WinSxS"), size=None,
                      status="需管理员使用 DISM 分析实际占用；不递归累加含硬链接的目录体积",
                      action="分析组件存储，或通过 Windows 清理已替代组件；不会使用 /ResetBase。",
                      actions=["analyze_component_store", "cleanup_component_store"]))
    return items


def _decode_output(data) -> str:
    if isinstance(data, str):
        return data
    if not data:
        return ""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            kernel = _library("kernel32")
            kernel.GetOEMCP.restype = wintypes.UINT
            return data.decode(f"cp{kernel.GetOEMCP()}", errors="replace")
        except (OSError, LookupError):
            return data.decode("mbcs" if os.name == "nt" else "utf-8", errors="replace")


def run_action(action_id: str, progress=None, *, allow_elevation=False) -> dict:
    """Execute exactly one action selected explicitly by the user.

    Administrative actions reject a non-elevated process unless the caller opts
    into Windows UAC for read-only component analysis. DISM is allowed to finish.
    """
    result = dict(time=time.strftime("%Y-%m-%d %H:%M:%S"), action=action_id, ok=False,
                  command=[], returncode=None, stdout="", stderr="", output="", message="")
    # Open the audit log before any side effect so log creation failure prevents it.
    try:
        core.DATA_DIR.mkdir(parents=True, exist_ok=True)
        log = (core.DATA_DIR / "system-actions.jsonl").open("a", encoding="utf-8")
    except OSError as exc:
        result["message"] = f"无法创建操作日志，操作未执行：{exc}"
        return result
    with log:
        try:
            if action_id not in ACTION_LABELS:
                raise ValueError(f"未知系统操作：{action_id}")
            if os.name != "nt":
                raise OSError("此功能仅支持 Windows")
            if action_id in ADMIN_ACTIONS and not _is_admin():
                if action_id == 'analyze_component_store' and allow_elevation:
                    from cplus_cleaner.features.system.system_analysis import run_elevated
                    result.update(run_elevated(progress))
                    log.write(json.dumps(result, ensure_ascii=False) + '\n')
                    log.flush()
                    return result
                raise PermissionError("此操作需要管理员权限。请以管理员身份启动 CCLEANNER 后重试；本次未执行。")
            label = ACTION_LABELS[action_id]
            if progress:
                progress(label)
            if action_id == "empty_recycle_bin":
                root = _system_drive()
                result["command"] = ["SHEmptyRecycleBinW", root]
                code = _empty_recycle_bin(root)
                result["returncode"] = code
                result["ok"] = code >= 0
                result["message"] = (f"已永久清空当前用户在 {root} 的回收站" if code >= 0
                                     else f"清空失败（HRESULT 0x{code & 0xffffffff:08X}）")
            else:
                system = _system_directory()
                if action_id in {"hibernate_off", "hibernate_on"}:
                    command = [ntpath.join(system, "powercfg.exe"), "/hibernate",
                               "off" if action_id == "hibernate_off" else "on"]
                elif action_id in {"analyze_component_store", "cleanup_component_store"}:
                    command = [ntpath.join(system, "Dism.exe"), "/Online", "/Cleanup-Image",
                               "/AnalyzeComponentStore" if action_id == "analyze_component_store"
                               else "/StartComponentCleanup"]
                else:
                    command = [ntpath.join(system, "SystemPropertiesAdvanced.exe")]
                result["command"] = command
                if action_id == "pagefile_settings":
                    process = subprocess.Popen(command, shell=False, creationflags=CREATE_NO_WINDOW)
                    result.update(ok=True, pid=process.pid,
                                  message="已打开高级系统设置；选择 性能设置 → 高级 → 虚拟内存。")
                else:
                    if action_id in ('analyze_component_store', 'cleanup_component_store') and progress:
                        from cplus_cleaner.features.system.system_analysis import run_streamed
                        process = run_streamed(command, progress)
                    else:
                        process = subprocess.run(command, shell=False, capture_output=True,
                                                 creationflags=CREATE_NO_WINDOW)
                    stdout, stderr = _decode_output(process.stdout), _decode_output(process.stderr)
                    success = process.returncode in (0, 3010)
                    result.update(ok=success, returncode=process.returncode, stdout=stdout,
                                  stderr=stderr, output=stdout + ("\n" if stdout and stderr else "") + stderr,
                                  message=(f"{label}完成" if success else f"{label}失败，退出码 {process.returncode}"))
                    if process.returncode == 3010:
                        result.update(restart_required=True, message=f"{label}完成，需要重启 Windows")
            if progress:
                progress(result["message"])
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result["message"] = str(exc)
        try:
            log.write(json.dumps(result, ensure_ascii=False) + "\n")
            log.flush()
        except OSError as exc:
            result["log_error"] = str(exc)
            result["message"] += f"；写入操作日志失败：{exc}"
    return result

# Purpose: Recycle bin, hibernation, pagefile and component-store interfaces
