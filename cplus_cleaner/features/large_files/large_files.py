"""Native-rule file discovery, separate from verified operation authorization."""
from __future__ import annotations

from collections import Counter
import json
import fnmatch
import os
from pathlib import Path
import shutil
import stat
import threading
import time
import uuid

from cplus_cleaner.common import core, runtime
from cplus_cleaner.platforms import software_windows as windows

MIB = 1024 * 1024
ORIGINAL_MINIMUM = 30 * MIB + 1  # The native callback rejects sizeMiB <= 30.
MINIMUM = ORIGINAL_MINIMUM
ORIGINAL_LABEL = "原版>30MB"
DIRECTORY_EXCLUSIONS = ("Program Files*", "*Microsoft*", "System32", "$RECYCLE.BIN")
TYPES = {
    "压缩包": (".rar", ".zip", ".7z", ".tar", ".iso", ".xz", ".gz", ".bz2"),
    "安装包": (".exe",),
    "日志文件": (".log",),
    "视频文件": (".mp4", ".ts", ".mov", ".mxf", ".mpg", ".flv", ".wmv", ".avi", ".m4v", ".f4v", ".3gp", ".asf", ".mkv"),
    "音频文件": (".mp3", ".ogg", ".wav", ".wma", ".ape", ".flac", ".aac", ".ac3", ".mmf", ".amr", ".m4a", ".m4r", ".wv", ".mp2"),
    "办公文档": (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv"),
    "临时文件": (".tmp",),
}
EXTENSIONS = {extension: name for name, extensions in TYPES.items() for extension in extensions}


def system_drive():
    return Path(os.environ.get("SystemDrive", "C:") + "\\")


def disk_inventory():
    result = []
    for drive in sorted(windows.fixed_drives(), key=lambda value: value.drive.casefold()):
        try:
            usage = shutil.disk_usage(drive)
            result.append(dict(root=str(drive), total=usage.total, used=usage.used, free=usage.free))
        except OSError:
            continue
    return result


def default_destination():
    try:
        saved = json.loads((core.DATA_DIR / "bigfiles-settings.json").read_text(encoding="utf-8"))
        if saved.get("custom") and isinstance(saved.get("destination"), str):
            return saved["destination"]
    except (OSError, ValueError, AttributeError):
        pass
    for drive in sorted(windows.fixed_drives(), key=lambda value: value.drive.casefold()):
        if "d:" <= drive.drive.casefold() <= "z:" and drive.drive.casefold() != system_drive().drive.casefold():
            return str(drive / "bigfiles")
    return ""


def save_destination(value):
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (core.DATA_DIR / "bigfiles-settings.json").write_text(
        json.dumps(dict(destination=str(value), custom=True), ensure_ascii=False), encoding="utf-8")


def action_exclusions():
    """Operation protection only: never use these roots to hide scan results."""
    roots = list(core.protected_roots())
    for name in ("Windows", "Program Files", "Program Files (x86)", "ProgramData",
                 "$Recycle.Bin", "System Volume Information", "$Windows.~BT", "$Windows.~WS", "Recovery"):
        roots.append(system_drive() / name)
    try:
        for entry in core.installed_apps():
            value = str(entry.get("location", "")).strip().strip('"')
            if value:
                root = core.canonical(Path(value))
                if root != Path(root.anchor) and root != core.canonical(Path.home()):
                    roots.append(root)
    except OSError:
        pass
    return roots


def exclusions():
    # Original scanner filters directory names at every depth. The current
    # application's source is an explicit additional independent exclusion.
    return list(map(core.canonical, runtime.protected_paths(core.APP_DIR)))


def directory_excluded(name):
    return any(fnmatch.fnmatchcase(name.casefold(), pattern.casefold()) for pattern in DIRECTORY_EXCLUSIONS)


def excluded(path, blocked):
    return any(core.inside(path, root) for root in blocked)


def threshold_description(minimum):
    return "原版阈值：单文件大于30MB" if minimum == ORIGINAL_MINIMUM else f"单文件至少 {minimum // MIB}MB"


def action_block_reason(path, *, info=None, roots=None, active=None):
    """Discover protected files too, but do not silently authorize moving them."""
    roots = action_exclusions() if roots is None else roots
    try:
        core.check_path(path)
        if excluded(path, roots):
            return "已安装软件目录，请使用软件搬家"
        info = info or path.lstat()
        if info.st_nlink != 1:
            return "硬链接文件，仅展示逻辑大小"
        if active is not None and os.path.normcase(str(path)) in active:
            return "程序正在运行，请先关闭"
    except (OSError, ValueError) as exc:
        return str(exc)
    return ""


def scan(cancel=None, progress=None, *, root=None, minimum=MINIMUM, blocked=None):
    """Read the system volume with the recovered per-directory wildcard filter."""
    cancel = cancel or threading.Event()
    root = core.canonical(Path(root) if root is not None else system_drive())
    blocked = exclusions() if blocked is None else list(blocked)
    blocked_keys = {os.path.normcase(str(core.canonical(path))).rstrip("\\/") for path in blocked}
    blocked_prefixes = tuple(key + os.sep for key in blocked_keys)
    def skip(path):
        key = os.path.normcase(str(path)).rstrip("\\/")
        return key in blocked_keys or key.startswith(blocked_prefixes) or directory_excluded(path.name)
    items, stack, counts = [], [root], Counter()
    protected = action_exclusions()
    try:
        active = {os.path.normcase(str(path)) for path in windows.running_images()}
    except OSError:
        active = set()
    blocked_paths = {}
    stamp = 0
    while stack and not cancel.is_set():
        folder = stack.pop()
        try:
            if core.is_link(folder) or skip(folder):
                counts["skipped_directories"] += 1
                continue
            with os.scandir(folder) as entries:
                for entry in entries:
                    if cancel.is_set():
                        break
                    path = Path(entry.path)
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if not skip(path):
                                stack.append(path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        counts["files"] += 1
                        category = EXTENSIONS.get(path.suffix)
                        if category:
                            # Windows DirEntry.stat() leaves st_nlink at zero.
                            # lstat supplies the actual hard-link identity/count.
                            info = path.lstat()
                            reparse = bool(getattr(info, "st_file_attributes", 0) & 0x400)
                            if reparse:
                                counts["links"] += 1
                            if not reparse and info.st_size >= minimum:
                                item = core.file_record(path, root, category)
                                items.append(item)
                                reason = action_block_reason(path, info=info, roots=protected, active=active)
                                if reason:
                                    blocked_paths[item.path] = reason
                    except (OSError, ValueError):
                        counts["errors"] += 1
                    if progress and time.monotonic() - stamp >= .12:
                        progress(dict(stage="scan", completed=None, total=None,
                            message=f"已检查 {counts['files']:,} 个文件，发现 {len(items):,} 个大文件", path=str(folder)))
                        stamp = time.monotonic()
            counts["directories"] += 1
        except OSError:
            counts["errors"] += 1
    return dict(items=sorted(items, key=lambda item: item.size, reverse=True),
                counts=dict(counts), cancelled=cancel.is_set(), root=str(root), minimum=minimum,
                blocked_paths=blocked_paths)


def validate(item):
    path = core.check_path(Path(item.path))
    root = core.canonical(Path(item.root))
    if not core.inside(path, root) or path == root or EXTENSIONS.get(path.suffix) != item.category:
        raise ValueError("文件已超出扫描范围或类型已变化，请重新扫描")
    reason = action_block_reason(path, active={os.path.normcase(str(p)) for p in windows.running_images()})
    if reason:
        raise ValueError(reason)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("跳过非普通文件或硬链接")
    if (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino) != (item.size, item.mtime_ns, item.device, item.inode):
        raise ValueError("文件在扫描后已改变，请重新扫描")
    return path


def append_log(record, *, history=False):
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (core.DATA_DIR / ("bigfiles-history.jsonl" if history else "operations.jsonl")).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def records():
    latest = {}
    try:
        with (core.DATA_DIR / "bigfiles-history.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                    latest[record["id"]] = record
                except (ValueError, KeyError):
                    continue
    except FileNotFoundError:
        pass
    return sorted(latest.values(), key=lambda record: record["time"], reverse=True)


def destination_path(value, source):
    if not str(value).strip():
        raise ValueError("请先选择其他磁盘上的搬家位置")
    parent = core.check_path(Path(value))
    if str(parent).startswith("\\\\"):
        raise ValueError("请选择本机磁盘目录")
    existing = next((path for path in (parent, *parent.parents) if path.exists()), None)
    if existing is None or not existing.is_dir() or existing.stat().st_dev == source.stat().st_dev:
        raise ValueError("搬家目标必须位于其他磁盘分区")
    if core.inside(parent, source.parent):
        raise ValueError("搬家目标不能位于源目录内")
    # Preserve the original directory hierarchy: C:\Users\... -> D:\bigfiles\C\Users\...
    target = parent / (source.drive.rstrip(":\\") or "files") / source.relative_to(source.anchor)
    core.check_path(target)
    if os.path.lexists(target):
        raise ValueError("目标已存在同名文件，不会覆盖")
    return target, existing


def move_one(item, destination, cancel=None, progress=None):
    source = validate(item)
    target, existing = destination_path(destination, source)
    if shutil.disk_usage(existing).free < item.size:
        raise ValueError("目标磁盘剩余空间不足")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
    record = dict(id=uuid.uuid4().hex, time=time.time(), action="bigfile_move", path=str(source),
                  target=str(target), size=item.size, category=item.category, ok=False)
    def emit(stage, completed, total, message):
        if progress:
            progress(dict(stage=stage, completed=completed, total=total, message=message))
    try:
        completed = 0
        with staging.open("xb") as output, source.open("rb") as stream:
            while block := stream.read(4 * MIB):
                if cancel and cancel.is_set():
                    raise InterruptedError("已取消，原文件保留")
                output.write(block)
                completed += len(block)
                emit("copy", completed, item.size, f"正在复制 {source.name}")
            output.flush()
            os.fsync(output.fileno())
        emit("verify", None, None, f"正在校验 {source.name}")
        digest = core.sha256_file(source, cancel)
        if digest != core.sha256_file(staging, cancel):
            raise ValueError("内容校验失败，原文件保留")
        validate(item)
        shutil.copystat(source, staging)
        core.check_path(target)
        if cancel and cancel.is_set():
            raise InterruptedError("已取消，原文件保留")
        if os.name == "nt":
            os.rename(staging, target)  # Windows no-replace atomic publish.
        else:
            os.link(staging, target)
            staging.unlink()
        # Durable destination record before removing the verified original.
        record.update(status="已复制，等待移除原文件", digest=digest)
        append_log(record, history=True)
        validate(item).unlink()
        record.update(ok=True, status="搬家成功")
        try:
            append_log(record, history=True)
        except OSError:
            # The durable pre-removal entry still contains the verified target.
            # Do not report a moved file as remaining in the scan result.
            record["warning"] = "最终记录未更新，可根据已复制记录查看目标"
        return record
    finally:
        staging.unlink(missing_ok=True)


def operate(items, action, destination="", cancel=None, progress=None):
    if action not in {"move", "delete"}:
        raise ValueError("未知文件操作")
    result = dict(success=[], errors=[], bytes=0, cancelled=False)
    for index, item in enumerate(items):
        if cancel and cancel.is_set():
            result["cancelled"] = True
            break
        record = dict(time=time.strftime("%Y-%m-%d %H:%M:%S"), action="bigfile_" + action, path=item.path, ok=False)
        try:
            if action == "move":
                move_one(item, destination, cancel, progress)
            else:
                validate(item).unlink()
            result["success"].append(item.path)
            result["bytes"] += item.size
            record["ok"] = True
        except (OSError, ValueError, InterruptedError) as exc:
            record["error"] = str(exc)
            result["errors"].append(dict(path=item.path, error=str(exc)))
        try:
            append_log(record)
        except OSError as exc:
            result["errors"].append(dict(path=item.path, error=f"记录写入失败：{exc}"))
        if progress:
            progress(dict(stage=action, completed=index+1, total=len(items), message=f"已处理 {index+1}/{len(items)} 个文件"))
    result["cancelled"] = bool(cancel and cancel.is_set())
    return result

# Purpose: Classification of files larger than 30 MiB, moving and recovery records
