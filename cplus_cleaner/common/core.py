"""CPlus Cleaner: independent, standard-library-only Windows maintenance backend."""
from __future__ import annotations

import ctypes
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from cplus_cleaner.common import runtime
from cplus_cleaner.common.user_data import data_directory

APP_DIR = runtime.resource_root()
DATA_DIR = data_directory()


def is_link(path: Path) -> bool:
    """Include path-redirection reparse points; WOF is file compression."""
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400
        and getattr(info, "st_reparse_tag", 0) != 0x80000017)


def inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def canonical(path: Path) -> Path:
    return Path(os.path.abspath(path))


def application_roots() -> list[Path]:
    # A portable EXE can live in Downloads: protect the file, not every
    # unrelated file in its parent folder.
    return runtime.protected_paths(APP_DIR)


def protected_roots() -> list[Path]:
    roots = application_roots()
    for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        if os.environ.get(key):
            roots.append(canonical(Path(os.environ[key])))
    return roots


def check_path(path: Path, allow_system_temp: bool = False, scope_id: str = "") -> Path:
    path = canonical(path)
    if path == Path(path.anchor) or path == canonical(Path.home()):
        raise ValueError("不能操作磁盘根目录或整个用户目录")
    for part in [path, *path.parents]:
        if part.exists() and is_link(part):
            raise ValueError(f"跳过链接或重解析目录：{part}")
    win_temp = canonical(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp")
    scoped = False
    if scope_id:
        from cplus_cleaner.features.drive_cleanup import cdrive
        scoped = cdrive.authorize(path, scope_id)
        if not scoped:
            raise ValueError("文件超出 C 盘清理规则授权范围")
    for root in protected_roots():
        if inside(path, root):
            if scoped and root not in application_roots():
                continue
            if allow_system_temp and root not in application_roots() and inside(path, win_temp):
                continue
            raise ValueError(f"受保护的位置：{path}")
    return path


@dataclass(frozen=True)
class Rule:
    name: str
    root: Path
    group: str = "垃圾清理"
    days: int = 0
    suffixes: tuple[str, ...] = ()
    system_temp: bool = False
    patterns: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    recursive: bool = True
    scope_id: str = ""
    modified_within: bool = False


@dataclass(frozen=True)
class Candidate:
    path: str
    root: str
    category: str
    size: int
    mtime_ns: int
    device: int
    inode: int
    system_temp: bool = False
    digest: str = ""
    group: str = ""
    scope_id: str = ""


def file_record(path: Path, root: Path, category: str, system_temp=False, digest="", group="", scope_id="") -> Candidate:
    info = path.lstat()
    return Candidate(str(path), str(root), category, info.st_size, info.st_mtime_ns,
                     info.st_dev, info.st_ino, system_temp, digest, group, scope_id)


def walk_files(root: Path, cancel: threading.Event | None = None, on_error=None, on_skip=None, *, recursive=True):
    """Do not cross directory links; unreadable directories are not fatal."""
    stack = [root]
    while stack:
        if cancel and cancel.is_set():
            return
        current = stack.pop()
        try:
            if is_link(current):
                if on_skip:
                    on_skip(current)
                continue
            with os.scandir(current) as entries:
                for entry in entries:
                    if cancel and cancel.is_set():
                        return
                    try:
                        p = Path(entry.path)
                        if is_link(p):
                            if on_skip:
                                on_skip(p)
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if recursive:
                                stack.append(p)
                        elif entry.is_file(follow_symlinks=False):
                            yield p
                    except OSError as exc:
                        if on_error:
                            on_error(Path(entry.path), exc)
        except OSError as exc:
            if on_error:
                on_error(current, exc)


def matches_rule(path: Path, rule: Rule) -> bool:
    """Windows-like case insensitive filename patterns, with explicit exclusions."""
    relative = path.relative_to(rule.root).as_posix().lower()
    name = path.name.lower()
    def match(pattern):
        pattern = pattern.replace("\\", "/").lower()
        return fnmatch.fnmatchcase(relative if "/" in pattern else name, "*" if pattern == "*.*" else pattern)
    return (not rule.patterns or any(match(p) for p in rule.patterns)) and not any(match(p) for p in rule.excluded)


def scan_rules(rules: list[Rule], cancel=None, progress=None, *, diagnostics=None, on_batch=None) -> list[Candidate]:
    """Return candidates, stream batches, and retain reasons for empty/partial scans."""
    result, seen = [], set()
    pending = []
    last_flush = time.monotonic()

    def flush():
        nonlocal last_flush
        if pending and on_batch:
            on_batch(list(pending))
        pending.clear()
        last_flush = time.monotonic()

    for rule in rules:
        if cancel and cancel.is_set():
            break
        detail = dict(name=rule.name, root=str(rule.root), days=rule.days, modified_within=rule.modified_within, status="扫描中",
                      scanned=0, matched=0, filtered_age=0, filtered_type=0,
                      duplicates=0, skipped_links=0, errors=0, error_samples=[])
        if diagnostics is not None:
            diagnostics.append(detail)

        def record_error(path, exc):
            detail["errors"] += 1
            if len(detail["error_samples"]) < 3:
                detail["error_samples"].append(f"{path}: {exc}")

        def record_link(path):
            detail["skipped_links"] += 1

        try:
            root = check_path(rule.root, rule.system_temp, rule.scope_id)
            if not stat.S_ISDIR(root.stat().st_mode):
                raise ValueError("扫描路径不是目录")
        except FileNotFoundError:
            detail["status"] = "目录不存在"
            continue
        except (OSError, ValueError) as exc:
            detail["status"] = "无法扫描"
            record_error(rule.root, exc)
            continue
        if progress:
            progress(rule.name)
        observed_at = time.time()
        cutoff = observed_at - rule.days * 86400
        for path in walk_files(root, cancel, record_error, record_link, recursive=rule.recursive):
            detail["scanned"] += 1
            if time.monotonic() - last_flush >= .2 or len(pending) >= 100:
                flush()
                if progress:
                    progress(f"{rule.name}：已检查 {detail['scanned']:,} 个，累计找到 {len(result):,} 个文件")
            key = os.path.normcase(str(path))
            if key in seen:
                detail["duplicates"] += 1
                continue
            if (rule.suffixes and path.suffix.lower() not in rule.suffixes) or not matches_rule(path, rule):
                detail["filtered_type"] += 1
                continue
            try:
                if rule.scope_id:
                    from cplus_cleaner.features.drive_cleanup import cdrive
                    if not cdrive.authorize(path, rule.scope_id):
                        detail["filtered_type"] += 1
                        continue
                item = file_record(path, root, rule.name, rule.system_temp, scope_id=rule.scope_id)
                modified = item.mtime_ns / 1_000_000_000
                outside_age = (modified < cutoff or modified > observed_at) if rule.modified_within else modified > cutoff
                if rule.days and outside_age:
                    detail["filtered_age"] += 1
                    continue
                result.append(item)
                pending.append(item)
                detail["matched"] += 1
                seen.add(key)
            except OSError as exc:
                record_error(path, exc)
        flush()
        detail["status"] = ("已取消" if cancel and cancel.is_set() else
                            "部分无法读取" if detail["errors"] else "完成")
    return result


def privacy_rules(recent: Path) -> list[Rule]:
    return [Rule("最近访问快捷方式", recent, "隐私痕迹", 0, (".lnk",)),
            Rule("Jump List 自动访问记录", recent / "AutomaticDestinations", "隐私痕迹", 0,
                 (".automaticdestinations-ms",)),
            Rule("Jump List 自定义访问记录", recent / "CustomDestinations", "隐私痕迹", 0,
                 (".customdestinations-ms",))]


def default_rules(days: int = 0) -> list[Rule]:
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
    roaming = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
    rules = [Rule("用户临时文件", local / "Temp", days=days),
             Rule("系统临时文件", Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp", days=days, system_temp=True),
             Rule("崩溃转储", local / "CrashDumps", days=days),
             Rule("Windows 错误报告缓存", local / "Microsoft/Windows/WER", days=days),
             Rule("缩略图缓存", local / "Microsoft/Windows/Explorer", days=days, suffixes=(".db",))]
    rules.extend(privacy_rules(roaming / "Microsoft/Windows/Recent"))
    for label, base in [("Chrome", local / "Google/Chrome/User Data"),
                        ("Edge", local / "Microsoft/Edge/User Data")]:
        if base.is_dir():
            for profile in base.iterdir():
                if profile.name == "Default" or profile.name.startswith("Profile "):
                    for cache in ("Cache/Cache_Data", "Code Cache", "GPUCache", "Service Worker/CacheStorage"):
                        rules.append(Rule(f"{label} {profile.name} {cache}", profile / cache, days=days))
    return rules


def chat_rules(base: Path, days: int = 0) -> list[Rule]:
    """Only explicitly recognized media/cache subtrees, never account databases."""
    rules = []
    roots = [base]
    if base.is_dir():
        roots += [p for p in base.iterdir() if p.is_dir() and not is_link(p)]
    suffixes = ("FileStorage/Image", "FileStorage/Video", "FileStorage/File", "FileStorage/Cache",
                "FileStorage/Temp", "FileStorage/TempFromPhone", "FileStorage/CustomEmotion",
                "Image/Group2", "Image/C2C", "Video", "Audio", "FileRecv",
                "nt_qq/nt_data/Pic", "nt_qq/nt_data/Video", "nt_qq/nt_data/File",
                "nt_qq/nt_data/log-cache")
    for account in roots:
        for suffix in suffixes:
            p = account / suffix
            if p.is_dir():
                rules.append(Rule(f"聊天附件 {account.name}/{suffix}", p, "聊天专清", days))
    return rules


def validate_candidate(item: Candidate) -> Path:
    root = check_path(Path(item.root), item.system_temp, item.scope_id)
    path = check_path(Path(item.path), item.system_temp, item.scope_id)
    if item.scope_id:
        from cplus_cleaner.features.drive_cleanup import cdrive
        if root != cdrive.scope_root(item.scope_id):
            raise ValueError("C 盘清理规则的根目录已变化，请重新扫描")
    if not inside(path, root) or path == root:
        raise ValueError("文件超出扫描目录")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("不是普通文件")
    if (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino) != (item.size, item.mtime_ns, item.device, item.inode):
        raise ValueError("文件在扫描后已改变，请重新扫描")
    return path


def sha256_file(path: Path, cancel=None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            if cancel and cancel.is_set():
                raise InterruptedError("已取消")
            digest.update(block)
    return digest.hexdigest()


def delete_files(items: list[Candidate], cancel=None, progress=None) -> dict:
    result = {"deleted": 0, "bytes": 0, "errors": [], "cancelled": False}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (DATA_DIR / "operations.jsonl").open("a", encoding="utf-8") as log:
        for item in items:
            if cancel and cancel.is_set():
                result["cancelled"] = True
                break
            record = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "action": "delete", "path": item.path}
            try:
                path = validate_candidate(item)
                if item.digest and sha256_file(path, cancel) != item.digest:
                    raise ValueError("内容已变化，跳过删除")
                validate_candidate(item)
                path.unlink()  # Deliberately permanent; no login, shell or recycle-bin redirect.
                result["deleted"] += 1
                result["bytes"] += item.size
                record["ok"] = True
            except (OSError, ValueError, InterruptedError) as exc:
                record.update(ok=False, error=str(exc))
                result["errors"].append({"path": item.path, "error": str(exc)})
            log.write(json.dumps(record, ensure_ascii=False) + "\n")
            log.flush()
            if progress:
                progress(item.path)
    return result


def large_files(root: Path, minimum: int, cancel=None, progress=None) -> list[Candidate]:
    root = check_path(root)
    items = []
    for index, path in enumerate(walk_files(root, cancel)):
        try:
            if path.stat().st_size >= minimum:
                items.append(file_record(path, root, "大文件"))
        except OSError:
            continue
        if progress and index % 200 == 0:
            progress(str(path))
    return sorted(items, key=lambda item: item.size, reverse=True)


def duplicate_files(root: Path, cancel=None, progress=None) -> list[list[Candidate]]:
    root = check_path(root)
    sizes, identities = {}, set()
    for path in walk_files(root, cancel):
        try:
            item = file_record(path, root, "重复文件")
            identity = (item.device, item.inode)
            if identity in identities or not item.size:
                continue  # Hard links do not represent independently reclaimable file bytes.
            identities.add(identity)
            sizes.setdefault(item.size, []).append(item)
        except OSError:
            continue
    groups = []
    for same_size in sizes.values():
        if cancel and cancel.is_set():
            break
        if len(same_size) < 2:
            continue
        hashes = {}
        for item in same_size:
            try:
                path = validate_candidate(item)
                digest = sha256_file(path, cancel)
                validate_candidate(item)
                if progress:
                    progress(item.path)
                hashed = Candidate(**{**asdict(item), "digest": digest, "group": digest})
                hashes.setdefault(digest, []).append(hashed)
            except InterruptedError:
                return groups
            except (OSError, ValueError):
                continue
        groups.extend(sorted(group, key=lambda x: x.path.casefold()) for group in hashes.values() if len(group) > 1)
    return sorted(groups, key=lambda g: g[0].size * (len(g) - 1), reverse=True)


def move_file(item: Candidate, destination: Path) -> Path:
    source = validate_candidate(item)
    destination = check_path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / source.name
    if inside(destination, source.parent) or target.exists():
        raise ValueError("目标不能位于源目录内，且不能覆盖已有同名文件")
    staging = target.with_name(target.name + ".ccleanner-part")
    created = False
    try:
        with staging.open("xb") as output:
            created = True
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, output, 1024 * 1024)
        if sha256_file(source) != sha256_file(staging):
            raise ValueError("复制后的校验失败")
        validate_candidate(item)
        shutil.copystat(source, staging)
        if os.name == "nt":
            os.rename(staging, target)  # Windows refuses overwriting an existing destination.
        else:
            os.link(staging, target)
            staging.unlink()
        source.unlink()
        return target
    finally:
        if created:
            staging.unlink(missing_ok=True)


def run_compact(folder: Path, compress=True) -> dict:
    if os.name != "nt":
        raise OSError("仅支持 Windows")
    folder = check_path(folder)
    if not folder.is_dir():
        raise ValueError("请选择目录")
    # Refuse reparse points anywhere in the recursive compact scope.
    for current, dirs, files in os.walk(folder, followlinks=False):
        for name in dirs + files:
            if is_link(Path(current) / name):
                raise ValueError("目录内存在链接，请移除链接或选择更小的目录")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/compact.exe"
    args = [str(executable), "/C" if compress else "/U", "/I", "/F", f"/S:{folder}"]
    if compress:
        args.append("/EXE:LZX")
    args.append("*")
    result = subprocess.run(args, cwd=folder, capture_output=True, creationflags=0x08000000)
    return {"returncode": result.returncode,
            "output": (result.stdout + result.stderr).decode("mbcs", errors="replace")}


def installed_apps() -> list[dict]:
    if os.name != "nt":
        return []
    import winreg
    result, seen = [], set()
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0, winreg.KEY_READ | view) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                                def read(name):
                                    try:
                                        return winreg.QueryValueEx(sub, name)[0]
                                    except OSError:
                                        return ""
                                name, command = read("DisplayName"), read("UninstallString")
                                if not name or not command or (name, command) in seen:
                                    continue
                                seen.add((name, command))
                                result.append({"name": name, "command": command, "location": read("InstallLocation"),
                                               "publisher": read("Publisher"), "version": read("DisplayVersion"),
                                               "display_icon": read("DisplayIcon"),
                                               "system_component": read("SystemComponent"),
                                               "parent_key": read("ParentKeyName"), "release_type": read("ReleaseType")})
                        except OSError:
                            continue
            except OSError:
                continue
    return sorted(result, key=lambda app: app["name"].casefold())


def uninstall_app(app: dict):
    """Use the publisher's registered uninstaller; do not guess residual ownership."""
    if os.name != "nt":
        raise OSError("仅支持 Windows")
    return subprocess.Popen(app["command"], shell=False)


def tree_digest(folder: Path, cancel=None) -> dict:
    result = {}
    for current, dirs, files in os.walk(folder, followlinks=False):
        for name in dirs + files:
            path = Path(current) / name
            if is_link(path):
                raise ValueError(f"目录内含链接，不支持搬家：{path}")
        for name in files:
            path = Path(current) / name
            result[str(path.relative_to(folder))] = (path.stat().st_size, sha256_file(path, cancel))
    return result


def create_junction(link: Path, target: Path):
    if os.name != "nt":
        raise OSError("Junction 仅支持 Windows")
    substitute = ("\\??\\" + str(target)).encode("utf-16-le")
    printable = str(target).encode("utf-16-le")
    names = substitute + b"\0\0" + printable + b"\0\0"
    data = struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(printable)) + names
    buffer = struct.pack("<IHH", 0xA0000003, len(data), 0) + data
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                   ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                       ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                                       ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    link.mkdir()
    handle = kernel.CreateFileW(str(link), 0x40000000, 0, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.WinError(ctypes.get_last_error())
        link.rmdir()
        raise error
    try:
        returned = ctypes.c_uint32()
        raw = ctypes.create_string_buffer(buffer)
        if not kernel.DeviceIoControl(handle, 0x900A4, raw, len(buffer), None, 0, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        kernel.CloseHandle(handle)
        link.rmdir()
        raise
    else:
        kernel.CloseHandle(handle)


def migrate_folder(source: Path, parent: Path, cancel=None) -> dict:
    if os.name != "nt":
        raise OSError("目录联接搬家仅支持 Windows")
    source, parent = check_path(source), check_path(parent)
    if not source.is_dir() or not parent.is_dir():
        raise ValueError("源目录和目标父目录必须存在")
    if inside(parent, source) or inside(source, parent):
        raise ValueError("源和目标目录不能相互包含")
    target = parent / source.name
    backup = source.with_name(source.name + ".ccleanner-backup")
    if target.exists() or backup.exists():
        raise ValueError("目标或备份目录已存在；不会覆盖")
    initial = tree_digest(source, cancel)
    if shutil.disk_usage(parent).free < sum(v[0] for v in initial.values()):
        raise ValueError("目标盘空间不足")
    if cancel and cancel.is_set():
        raise InterruptedError("已取消")
    shutil.copytree(source, target, symlinks=True)
    if tree_digest(target, cancel) != initial or tree_digest(source, cancel) != initial:
        raise ValueError(f"文件校验失败；源目录未改动，目标副本保留在 {target}")
    source.rename(backup)
    try:
        create_junction(source, target)
    except BaseException:
        backup.rename(source)
        raise
    record = {"source": str(source), "target": str(target), "backup": str(backup), "time": time.time()}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with (DATA_DIR / "migrations.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def restore_folder(record: dict):
    source, target, backup = (Path(record[k]) for k in ("source", "target", "backup"))
    check_path(backup)
    check_path(target)
    if not backup.is_dir() or not is_link(source):
        raise ValueError("备份不存在或源位置不是 Junction，不能自动还原")
    if source.resolve() != target.resolve():
        raise ValueError("Junction 目标已改变，不能自动还原")
    if tree_digest(target) != tree_digest(backup):
        raise ValueError("搬家后的文件已有变化。请先手动同步目标与备份，避免用旧备份覆盖新数据。")
    source.rmdir()  # Remove junction itself, never recursively remove its destination.
    try:
        backup.rename(source)
    except BaseException:
        create_junction(source, target)
        raise
    return {"restored": str(source), "retained_target_copy": str(target)}

# Purpose: Shared scanning, file checks, deletion, duplicate detection and history
