"""Discover installed application roots and relocate only individually admitted apps.

This capability is separate from core.check_path: ordinary cleanup and directory
tools retain their Program Files protection. No scan changes a file or registry.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

import core
import software_windows as windows


@dataclass(frozen=True)
class Software:
    name: str
    source: str
    size: int
    modified: float
    files: int
    device: int
    inode: int
    blocked: str = ""
    icon: bytes | None = None

    @property
    def key(self):
        return os.path.normcase(self.source)


def executable(value):
    """Read a registered path/command; never pass it to a shell or execute it."""
    if not isinstance(value, str):
        return None
    value = os.path.expandvars(value.strip())
    match = re.match(r'^"([^"\r\n]+\.(?:exe|dll|ico))"', value, re.I)
    if not match:
        match = re.match(r'^([^"\r\n]+?\.(?:exe|dll|ico))(?=$|[,\s])', value, re.I)
    if not match:
        return None
    path = Path(match[1])
    return core.canonical(path) if path.is_absolute() else None


def system_drive():
    import cdrive
    return core.canonical(cdrive.locations().drive)


def user_program_roots():
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    return [local / "Programs"]


def application_icon(root, registered, command, name):
    # Some uninstall records only point to an uninstaller. Its trash-can icon
    # must not be presented as the application's own brand icon.
    auxiliary = re.compile(r"unins|uninstall|setup|updat|crash|helper|repair|maintenanc", re.I)
    if registered and registered.is_file() and not auxiliary.search(registered.stem):
        return registered
    try:
        candidates = [path for path in root.glob("*.exe") if not auxiliary.search(path.stem)]
        if not candidates:
            for child in sorted(root.iterdir())[:32]:
                if child.is_dir() and not core.is_link(child):
                    candidates.extend(path for path in child.glob("*.exe") if not auxiliary.search(path.stem))
        if candidates:
            normalized = "".join(char for char in name.casefold() if char.isalnum())
            def score(path):
                stem = "".join(char for char in path.stem.casefold() if char.isalnum())
                return (int(bool(stem) and stem in normalized), len(stem) if stem in normalized else 0, path.stat().st_size)
            return max(candidates, key=score)
    except OSError:
        pass
    return command if command and command.is_file() and not auxiliary.search(command.stem) else None


def eligible_path(path, allow_junction=False):
    path = core.canonical(path)
    if path.drive.casefold() != system_drive().drive.casefold():
        raise ValueError("仅扫描 Windows 系统盘上的软件")
    # Never admit a whole shared root, OS-managed package store, other profile,
    # our own checkout, or a parent containing it.
    roots = [system_drive(), core.canonical(Path.home()), core.canonical(core.APP_DIR)]
    for variable in ("SystemRoot", "ProgramData", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"):
        if os.environ.get(variable):
            roots.append(core.canonical(Path(os.environ[variable])))
    forbidden = [core.canonical(core.APP_DIR)]
    for variable in ("SystemRoot", "ProgramData"):
        if os.environ.get(variable):
            forbidden.append(core.canonical(Path(os.environ[variable])))
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(variable):
            parent = Path(os.environ[variable])
            forbidden.extend(core.canonical(parent / name) for name in
                             ("WindowsApps", "Common Files", "Windows Defender", "Windows Security", "Microsoft Update Health Tools"))
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    forbidden.extend(core.canonical(local / name) for name in ("Packages", "Microsoft", "Temp"))
    profiles = core.canonical(Path.home().parent)
    if core.inside(path, profiles) and not core.inside(path, core.canonical(Path.home())):
        raise ValueError("不扫描其他用户的软件")
    if path in roots or any(core.inside(path, root) or core.inside(root, path) for root in forbidden):
        raise ValueError("系统、共享组件或程序自身目录不可搬家")
    for current in (path, *path.parents):
        if current == path and allow_junction:
            continue
        if current.exists() and core.is_link(current):
            raise ValueError("目录已搬家或包含链接")
    if not path.is_dir():
        raise ValueError("软件目录不存在")
    return path


def discovery():
    result = []
    for app in core.installed_apps():
        if app.get("system_component") or app.get("parent_key") or str(app.get("release_type", "")).lower() in {"update", "hotfix", "security update"}:
            continue
        icon = executable(app.get("display_icon", ""))
        command = executable(app.get("command", ""))
        location = app.get("location", "")
        root = Path(os.path.expandvars(location.strip().strip('"'))) if isinstance(location, str) and location.strip() else None
        if root is not None and not root.is_absolute():
            root = None
        if root is None or not root.is_dir():
            parents = [path.parent for path in (command, icon) if path and path.is_file()]
            root = parents[0] if parents else None
            if len(parents) > 1:
                if core.inside(parents[0], parents[1]):
                    root = parents[1]
                elif core.inside(parents[1], parents[0]):
                    root = parents[0]
        if root:
            root = core.canonical(root)
            result.append((app["name"], root, application_icon(root, icon, command, app["name"])))
    # Per-user portable installs often have no uninstall record.
    for container in user_program_roots():
        try:
            for root in container.iterdir():
                if root.is_dir() and not core.is_link(root):
                    executables = sorted(root.glob("*.exe"))
                    if executables:
                        result.append((root.name, core.canonical(root), executables[0]))
        except OSError:
            continue
    admitted = {}
    for name, root, icon in result:
        try:
            root = eligible_path(root)
        except (OSError, ValueError):
            continue
        admitted.setdefault(os.path.normcase(str(root)), (name, root, icon))
    # Two entries for overlapping roots must never be moved independently.
    entries = sorted(admitted.values(), key=lambda item: len(item[1].parts))
    unique = []
    for entry in entries:
        if not any(core.inside(entry[1], parent[1]) for parent in unique):
            unique.append(entry)
    return unique


def measure(root, cancel=None):
    total, count, modified = 0, 0, root.stat().st_mtime
    def failure(error):
        raise error
    for current, directories, files in os.walk(root, followlinks=False, onerror=failure):
        if cancel and cancel.is_set():
            raise InterruptedError("已取消")
        for name in directories + files:
            path = Path(current) / name
            if core.is_link(path):
                raise ValueError("目录内含链接，暂不支持搬家")
        for name in files:
            info = (Path(current) / name).stat()
            total += info.st_size
            count += 1
            modified = max(modified, info.st_mtime)
    return total, count, modified


def scan(cancel, progress=None, on_batch=None, *, on_progress=None):
    if on_progress:
        on_progress(dict(stage="discovery", completed=None, total=None, message="正在读取软件安装信息…"))
    entries = discovery()
    active, services = windows.running_images(), [executable(value) for value in windows.service_images()]
    items, skipped, excluded_services = [], Counter(), 0
    for index, (name, root, icon_path) in enumerate(entries):
        if cancel.is_set():
            break
        if any(path and core.inside(path, root) for path in services):
            excluded_services += 1
            if on_progress:
                on_progress(dict(stage="scan", completed=index+1, total=len(entries), message=f"已排除含系统服务的软件：{name}"))
            continue
        if on_progress:
            on_progress(dict(stage="scan", completed=index, total=len(entries), message=f"正在计算 {name} 的占用空间…"))
        if progress:
            progress(f"正在计算 {name} 的占用空间…")
        try:
            size, count, modified = measure(root, cancel)
            if not count:
                continue
            blocked = ""
            if windows.filesystem(root).upper() != "NTFS":
                blocked = "源盘需为 NTFS"
            elif any(core.inside(path, root) for path in active):
                blocked = "运行中，请先关闭"
            elif any(core.inside(root, Path(os.environ[key])) for key in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(key)) and not windows.administrator():
                blocked = "需管理员权限"
            info = root.stat()
            item = Software(name, str(root), size, modified, count, info.st_dev, info.st_ino,
                            blocked, windows.extract_icon(icon_path))
            items.append(item)
            if on_batch:
                on_batch([item])
        except InterruptedError:
            break
        except (OSError, ValueError) as exc:
            skipped[str(exc).split(":")[0]] += 1
        if on_progress:
            on_progress(dict(stage="scan", completed=index+1, total=len(entries), message=f"已检查 {index+1}/{len(entries)} 个软件目录"))
    if on_progress and not cancel.is_set():
        on_progress(dict(stage="scan", completed=len(entries), total=len(entries), message="软件扫描完成"))
    return dict(items=items, skipped=dict(skipped), excluded_services=excluded_services, cancelled=cancel.is_set())


def default_destination():
    try:
        saved = json.loads((core.DATA_DIR / "software-migration-settings.json").read_text(encoding="utf-8"))
        value = saved.get("destination", "")
        legacy_default = isinstance(value, str) and Path(value).name.casefold() == "CCCLEANER软件搬家".casefold()
        if isinstance(value, str) and value and (saved.get("custom") or not legacy_default):
            return value
    except (OSError, ValueError, AttributeError):
        pass
    for drive in sorted(windows.fixed_drives(), key=lambda path: path.drive.casefold()):
        try:
            if "d:" <= drive.drive.casefold() <= "z:" and drive.drive.casefold() != system_drive().drive.casefold() and windows.filesystem(drive).upper() == "NTFS":
                return str(drive / "Program Files")
        except OSError:
            pass
    return ""


def save_destination(value):
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    (core.DATA_DIR / "software-migration-settings.json").write_text(json.dumps(dict(destination=str(value), custom=True), ensure_ascii=False), encoding="utf-8")


def same_volume(a, b):
    return a.stat().st_dev == b.stat().st_dev


def validate_destination(value, source):
    if not str(value).strip():
        raise ValueError("请先选择其他磁盘上的搬家位置")
    parent = core.canonical(Path(value))
    if str(parent).startswith("\\\\") or not parent.drive:
        raise ValueError("请选择本机磁盘目录")
    if parent == Path(parent.anchor) or core.inside(parent, source) or core.inside(source, parent):
        raise ValueError("目标必须是其他磁盘上的独立子目录")
    for current in (parent, *parent.parents):
        if current.exists() and core.is_link(current):
            raise ValueError("目标路径不能包含链接")
    existing = next((path for path in (parent, *parent.parents) if path.exists()), None)
    if existing is None or not existing.is_dir() or same_volume(source, existing):
        raise ValueError("目标必须位于另一个磁盘分区")
    core.check_path(parent)
    if windows.filesystem(existing).upper() != "NTFS":
        raise ValueError("目标磁盘需为 NTFS")
    return parent, existing


def validate_source(item):
    root = eligible_path(Path(item.source))
    info = root.stat()
    if (info.st_dev, info.st_ino) != (item.device, item.inode):
        raise ValueError("软件目录已变化，请重新扫描")
    if not any(path == root for _, path, _ in discovery()):
        raise ValueError("软件安装位置已变化，请重新扫描")
    if windows.filesystem(root).upper() != "NTFS":
        raise ValueError("源磁盘需为 NTFS")
    if any(core.inside(root, Path(os.environ[key])) for key in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(key)) and not windows.administrator():
        raise ValueError("该安装目录需要管理员权限，请以管理员身份启动后重新扫描")
    if any(core.inside(path, root) for path in windows.running_images()):
        raise ValueError("软件仍在运行，请先退出相关软件后重新扫描")
    if any(path and core.inside(path, root) for path in map(executable, windows.service_images())):
        raise ValueError("软件包含系统服务，不支持自动搬家")
    return root


def append_record(record):
    core.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (core.DATA_DIR / "software-migrations.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def records():
    latest = {}
    try:
        with (core.DATA_DIR / "software-migrations.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                    if isinstance(record, dict) and record.get("id"):
                        latest[record["id"]] = record
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    for record in latest.values():
        if record.get("status") not in {"准备中", "切换中", "已搬家，清除旧副本中"}:
            continue
        try:
            source, target, backup = (Path(record[key]) for key in ("source", "target", "backup"))
            eligible_path(source, allow_junction=True)
            if core.is_link(source) and source.resolve() == target.resolve():
                record.update(ok=True, status="搬家完成，旧副本保留" if backup.exists() else "搬家完成",
                              released=0 if backup.exists() else record.get("size", 0))
        except (OSError, ValueError, KeyError):
            pass
    return sorted(latest.values(), key=lambda value: value.get("time", 0), reverse=True)


def move(item, destination, cancel=None, progress=None, *, on_progress=None):
    """Copy, verify, switch to a Junction, then remove only the verified old tree."""
    def report(stage, message, completed=None, total=None):
        if on_progress:
            on_progress(dict(stage=stage, message=message, completed=completed, total=total))
    report("prepare", f"正在检查 {item.name} 的安装位置与运行状态…")
    source = validate_source(item)
    parent, existing = validate_destination(destination, source)
    if cancel and cancel.is_set():
        raise InterruptedError("已取消")
    if progress:
        progress(f"正在校验 {item.name}…")
    report("prepare", f"正在读取并校验 {item.name} 的源文件…")
    initial = core.tree_digest(source, cancel)
    amount = sum(value[0] for value in initial.values())
    if shutil.disk_usage(existing).free < amount + 1024*1024:
        raise ValueError("目标磁盘空间不足")
    identifier = uuid.uuid4().hex
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", item.name).strip(" .")[:60] or "Software"
    target = parent / (safe_name + "-" + hashlib.sha256(item.key.encode()).hexdigest()[:8])
    backup = source.with_name(source.name + ".ccc-move-" + identifier)
    if target.exists() or backup.exists():
        raise ValueError("目标或临时备份已存在，不会覆盖；请更换位置或查看搬家记录")
    record = dict(id=identifier, name=item.name, source=str(source), target=str(target), backup=str(backup),
                  size=amount, released=0, time=time.time(), status="准备中", ok=False)
    # Audit must be writable before any source mutation.
    append_record(record)
    switched = False
    try:
        parent.mkdir(parents=True, exist_ok=True)
        if progress:
            progress(f"正在复制 {item.name}…")
        copied = 0
        report("copy", f"正在复制 {item.name}", 0, amount)
        def copy_file(a, b):
            nonlocal copied
            if cancel and cancel.is_set():
                raise InterruptedError("已取消")
            if core.is_link(Path(a)):
                raise ValueError("文件变成了链接，已停止")
            with open(a, "rb") as source_file, open(b, "xb") as target_file:
                while block := source_file.read(4 * 1024 * 1024):
                    if cancel and cancel.is_set():
                        raise InterruptedError("已取消")
                    target_file.write(block)
                    copied += len(block)
                    report("copy", f"正在复制 {item.name}", copied, amount)
            shutil.copystat(a, b)
            return b
        shutil.copytree(source, target, symlinks=True, copy_function=copy_file)
        report("verify", f"正在校验 {item.name} 的复制结果…")
        if core.tree_digest(target, cancel) != initial or core.tree_digest(source, cancel) != initial:
            raise ValueError("复制校验失败，源软件保持原样；目标副本已保留")
        validate_source(item)
        if cancel and cancel.is_set():
            raise InterruptedError("已取消，源软件保持原样；目标副本已保留")
        record["status"] = "切换中"
        append_record(record)
        report("switch", f"正在切换 {item.name} 的原路径入口…")
        source.rename(backup)
        try:
            core.create_junction(source, target)
        except BaseException:
            backup.rename(source)
            raise
        switched = True
        record.update(ok=True, status="已搬家，清除旧副本中")
        append_record(record)
        report("cleanup", f"正在复核并清理 {item.name} 的旧副本…")
        # Only the exact renamed sibling tree, independently rechecked against
        # the verified snapshot, can be recursively removed.
        expected = source.with_name(source.name + ".ccc-move-" + identifier)
        if backup != expected or backup.parent != source.parent or core.is_link(backup):
            raise ValueError("旧副本路径校验失败，已保留")
        if core.tree_digest(backup) != initial or core.tree_digest(target) != initial:
            raise ValueError("切换后内容变化，旧副本已保留")
        shutil.rmtree(backup)
        record.update(status="搬家完成", released=amount)
        report("done", f"{item.name} 搬家完成", amount, amount)
    except Exception as exc:
        record.update(ok=switched, status="搬家完成，旧副本保留" if switched else "未完成，源位置未切换", error=str(exc))
        if switched and backup.exists():
            try:
                record["released"] = max(0, amount - measure(backup)[0])
            except (OSError, ValueError):
                pass
        try:
            append_record(record)
        except OSError as log_error:
            record["error"] += f"；记录写入失败：{log_error}"
        if not switched:
            raise
        return record
    try:
        append_record(record)
    except OSError as exc:
        record["error"] = f"搬家已完成，但记录写入失败：{exc}"
    return record


def restore(record, cancel=None):
    """Restore current target data, never an old backup over newer application data."""
    if record not in records() or not record.get("ok"):
        raise ValueError("只能还原有效的本机搬家记录")
    source, target = Path(record["source"]), Path(record["target"])
    if not source.exists() or not core.is_link(source) or source.resolve() != target.resolve():
        raise ValueError("原位置联接已变化，不能自动还原")
    if any(core.inside(path, target) or core.inside(path, source) for path in windows.running_images()):
        raise ValueError("软件仍在运行，请先关闭")
    eligible_path(source, allow_junction=True)
    core.check_path(target)
    temporary = source.with_name(source.name + ".ccc-restore-" + uuid.uuid4().hex)
    digest = core.tree_digest(target, cancel)
    if shutil.disk_usage(source.parent).free < sum(value[0] for value in digest.values()) + 1024*1024:
        raise ValueError("原磁盘空间不足")
    shutil.copytree(target, temporary, symlinks=True)
    if core.tree_digest(temporary, cancel) != digest or core.tree_digest(target, cancel) != digest:
        raise ValueError(f"还原校验失败，原联接未改动，副本保留于 {temporary}")
    if cancel and cancel.is_set():
        raise InterruptedError("已取消，原联接未改动")
    if not core.is_link(source) or source.resolve() != target.resolve():
        raise ValueError("复制期间原位置联接发生变化，未切换")
    source.rmdir()
    try:
        temporary.rename(source)
    except BaseException:
        core.create_junction(source, target)
        raise
    result = dict(record, status="已还原，目标副本保留", time=time.time(), restored=True, released=0)
    try:
        append_record(result)
    except OSError as exc:
        result["error"] = f"还原已完成，但记录写入失败：{exc}"
    return result
