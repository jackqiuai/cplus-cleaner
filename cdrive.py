"""Audited C-drive cleanup reconstructed from the locally shipped rule data.

The recovered DSL is evidence, not executable Python. Only reviewed file rules
are enabled; native custom handlers and system configuration are separate actions.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re

import core

MANIFEST = core.APP_DIR / "rules/cdrive-private.json"


@dataclass(frozen=True)
class Locations:
    drive: Path
    windows: Path
    home: Path
    local: Path
    roaming: Path
    programdata: Path
    temp: Path


def locations() -> Locations:
    home = core.canonical(Path.home())
    windows = core.canonical(Path(os.environ.get("SystemRoot", r"C:\Windows")))
    drive = Path(windows.anchor)
    return Locations(drive, windows, home,
                     core.canonical(Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))),
                     core.canonical(Path(os.environ.get("APPDATA", home / "AppData/Roaming"))),
                     core.canonical(Path(os.environ.get("ProgramData", drive / "ProgramData"))),
                     core.canonical(Path(os.environ.get("TEMP", home / "AppData/Local/Temp"))))


@lru_cache(maxsize=1)
def manifest():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if len(data["items"]) != 91 or sum(len(i["generals"]) for i in data["items"]) != 197:
        raise ValueError("恢复规则清单不完整")
    return data


# Every enabled item is reviewed below. This is deliberately not an eval() or
# blind interpreter of GetRegSz/GetIniData/GetFileVersion/Custom/Level.
REVIEWED = frozenset({10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22,
                      26, 32, 34, 41, 43, 47, 48, 49, 50, 52, 53, 55,
                      60, 61, 62, 63, 66, 67, 68, 72, 74, 80})
# Independent defaults for the audited reconstruction, not an interpretation of
# the original native Level/isSelect fields. Personal traces, backups, build
# packages and driver installation sources require an explicit user selection.
DEFAULT_SELECTED_ITEMS = frozenset({10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21,
                                    32, 34, 43, 52, 53, 60, 61, 63, 66, 67, 68, 74})
SCAN_OPTIONS = (("缓存文件", True), ("临时文件", True), ("日志文件", True),
                ("系统报告", True), ("应用缓存", True), ("备份文件", False))


def scan_option(group: str, name: str) -> str:
    name = name.split(" / ")[-1]
    if "日志" in name:
        return "日志文件"
    return {"缓存文件": "缓存文件", "临时文件": "临时文件", "系统相关": "系统报告",
            "应用程序": "应用缓存", "备份文件": "备份文件"}.get(group, group)


def default_selected_categories() -> frozenset[str]:
    return frozenset(f"{item['group']} / {item['name']}" for item in manifest()["items"]
                     if item["index"] in DEFAULT_SELECTED_ITEMS)


DELEGATED = {56: "组件备份由独立 DISM 组件清理操作管理",
             71: "独立的系统盘回收站操作",
             90: "独立的系统盘回收站操作"}
NOT_IMPLEMENTED = {29: "旧系统文件未实现清理，应通过 Windows 存储设置管理",
                   44: "尚未实现更新下载缓存清理，需处理更新服务及重启状态；DISM 组件清理不等价于清空 Download",
                   45: "尚未实现传递优化缓存清理，需处理服务状态；不直接清空更新 DataStore"}


def _templates(index, entry):
    """Return reviewed refinements, or a reason why this General is not enabled."""
    root = entry["root"]
    flags = entry["flags"]
    queries = entry["queries"]
    patterns = ()
    recursive = True
    note = "采用恢复路径；递归列出普通文件，日期阈值由本程序选择"
    # Refine overly broad original items to the actual cache/log subtrees.
    if index == 14 and "BaiduYunKernel" in root:
        return [], "Data 目录可能含配置，未作为缓存全部删除"
    if index == 15:
        queries = [q for q in queries if "backup" not in q.lower().replace("\\", "/").split("/")]
        note = "采用 WPS 缓存/日志路径；排除 office6/backup 文档恢复备份"
    if index == 16:
        if "Program Files" in root or flags == "Recursive":
            return [], "程序安装文件或浏览器广泛 .log/.old 扫描未启用"
        if "IndexedDB" in root:
            return [], "IndexedDB 含站点离线数据，未自动作为垃圾"
    if index == 17:
        if "Program Files" in root:
            return [], "原规则安装目录路径未启用（包括原有拼写问题）"
        if root.rstrip("\\").endswith("Service Worker"):
            return [(root + "CacheStorage\\", (), True), (root + "ScriptCache\\", (), True)], "仅列出 Service Worker 的 CacheStorage 和 ScriptCache"
    if index == 18 and "WebCache" in root:
        return [], "浏览历史数据库由隐私功能处理，未作为普通缓存删除"
    if index == 22 and "Program Files" in root:
        return [], "Office 更新安装包未作为普通缓存删除"
    if index == 41:
        root = root.rstrip("\\") + "\\packages"
        note = "仅 NuGet packages；保留 .nuget 下配置（包需重新下载）"
    if index == 43:
        patterns = ("thumbcache*.db", "iconcache*.db")
        note = "仅缩略图/图标数据库，保留同目录其他文件"
    if index == 52:
        # Two original scan branches describe the same current-user files.
        if root.lower().startswith("%systemdrive%"):
            return [], "所有用户分支合并为当前用户，避免重复"
        return [(root + "\\Microsoft\\Windows\\Explorer", ("thumbcache*.db", "iconcache*.db"), False),
                (root, ("IconCache.db",), False)], "仅当前用户的缩略图/图标缓存；占用项跳过，不重启 Explorer"
    if index == 60 and "ManifestCache" in root:
        return [], "WinSxS 元数据不直接删除，使用独立 DISM 操作"
    if index == 61:
        if "System Volume Information" in root:
            return [], "卷系统信息目录未启用"
        patterns = ("*.log", "*.etl", "*.cab", "*.txt", "*.tmp")
        note = "仅日志及其归档，保留其他扩展名；不清除事件数据库"
    if index == 62:
        patterns = ("*.lnk", "*.automaticdestinations-ms", "*.customdestinations-ms")
        note = "仅最近访问快捷方式及 Jump List；可能重置最近访问和固定项"
    if index == 63:
        if root.startswith("?GetRegSz("):
            root = "%SystemRoot%\\Temp\\"
            note = "系统 TEMP 分支限定 Windows\\Temp，不执行任意注册表表达式"
        if root.startswith("*\\"):
            return [], "Found.* 恢复文件未作为临时文件删除"
    if index == 66 and "SystemX86" in root:
        return [], "系统账户 Tencent 根目录过宽，未启用"
    if index == 72:
        patterns = ("*.dmp",)
    if index == 34:
        patterns = ("*.log",)
    # Explicit path queries narrow roots before any authorization is granted.
    result = []
    for query in queries or [""]:
        if "?" in root or "?" in query or any("?" in x for x in entry["excluded"]):
            return [], "动态版本/INI/注册表表达式尚未实现，不忽略排除条件"
        candidate_root = root
        candidate_patterns = patterns
        candidate_recursive = recursive
        if query:
            parts = query.replace("\\", "/").split("/")
            leaf = parts.pop()
            if parts:
                candidate_root = root.rstrip("\\/") + "\\" + "\\".join(parts)
            if "*" in leaf or "?" in leaf or flags == "File":
                candidate_patterns = patterns or (leaf,)
                candidate_recursive = flags == "Recursive"
            else:
                candidate_root = candidate_root.rstrip("\\/") + "\\" + leaf
        if flags == "File":
            candidate_recursive = False
        result.append((candidate_root, candidate_patterns, candidate_recursive))
    return result, note


def _expand_template(text: str, loc: Locations) -> str:
    text = re.sub(r"\\+", "/", text)
    # Limit the original all-user wildcard to the current account. Never traverse
    # another user's home or redirect the cleanup to a different disk.
    text = re.sub(r"^%systemdrive%/users/\*/appdata/local(?=/|$)", "%LOCALAPPDATA%", text, flags=re.I)
    text = re.sub(r"^%systemdrive%/users/\*/appdata/roaming(?=/|$)", "%APPDATA%", text, flags=re.I)
    text = re.sub(r"^%systemdrive%/users/\*(?=/|$)", "%USERPROFILE%", text, flags=re.I)
    values = dict(systemdrive=str(loc.drive).rstrip("\\/"), windir=str(loc.windows), systemroot=str(loc.windows),
                  userprofile=str(loc.home), localappdata=str(loc.local), appdata=str(loc.roaming),
                  programdata=str(loc.programdata), temp=str(loc.temp))
    def replace(match):
        if match[1].lower() not in values:
            raise ValueError("未实现的路径变量：" + match[0])
        return values[match[1].lower()].replace("\\", "/")
    text = re.sub(r"%([^%]+)%", replace, text)
    if "%" in text or "?" in text or text.startswith("*/"):
        raise ValueError("未实现的动态路径表达式")
    return text


def _expand_directories(path: Path):
    """Glob one component at a time, never following reparse points."""
    if not any("*" in part or "?" in part for part in path.parts):
        yield path
        return
    current = [Path(path.anchor)]
    for part in path.parts[1:]:
        following = []
        for parent in current:
            try:
                if parent.exists() and core.is_link(parent):
                    continue
                if "*" in part or "?" in part:
                    with os.scandir(parent) as entries:
                        for entry in entries:
                            p = Path(entry.path)
                            if fnmatch.fnmatchcase(entry.name.lower(), part.lower()) and not core.is_link(p) and entry.is_dir(follow_symlinks=False):
                                following.append(p)
                else:
                    p = parent / part
                    if p.exists() and core.is_link(p):
                        continue
                    following.append(p)
            except OSError:
                continue
        current = following
    yield from current


@lru_cache(maxsize=8)
def _catalog(loc: Locations):
    policies, audit = {}, []
    for item in manifest()["items"]:
        idx = item["index"]
        record = dict(index=idx, name=item["name"], group=item["group"], source_level=item["level"],
                      warning=item["warning"], source_decoded_offset=item["decoded_offset"],
                      supported_paths=[], omitted=[], status="未启用")
        audit.append(record)
        if idx not in REVIEWED:
            record["omitted"].append(DELEGATED.get(idx, NOT_IMPLEMENTED.get(idx, "原项目涉及安装/系统状态、个人数据或原生自定义处理器；未作为普通文件删除")))
            if idx in DELEGATED:
                record["status"] = "系统管理/独立操作"
            continue
        for ordinal, entry in enumerate(item["generals"]):
            templates, note = _templates(idx, entry)
            if not templates:
                record["omitted"].append(dict(source_path=entry["root"], reason=note))
            for template, patterns, recursive in templates:
                try:
                    expanded = _expand_template(template, loc)
                    target = Path(expanded)
                    # RootPath may end in a file glob rather than a directory.
                    if "*" in target.name and not template.endswith(("\\", "/")):
                        if template.endswith(("*.log", "*.tmp", "*.7z")):
                            patterns = patterns or (target.name,)
                            target = target.parent
                    roots = list(_expand_directories(target))
                    if not roots:
                        record["omitted"].append(dict(source_path=template, reason="路径通配符当前没有命中目录"))
                    for root in roots:
                        root = core.canonical(root)
                        if not core.inside(root, loc.drive) or root in (loc.drive, loc.home):
                            record["omitted"].append(dict(source_path=template, reason="不在系统盘或根目录过宽"))
                            continue
                        identity = f"{idx}:{ordinal}:{root}:{patterns}:{recursive}"
                        scope_id = "fq-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
                        rule = core.Rule(f"{item['group']} / {item['name']}", root, item["group"],
                                         patterns=patterns, excluded=tuple(entry["excluded"]),
                                         recursive=recursive, scope_id=scope_id)
                        policies[scope_id] = rule
                        record["supported_paths"].append(dict(path=str(root), patterns=patterns or ("*",),
                                                             recursive=recursive, excluded=entry["excluded"], note=note,
                                                             source_general=ordinal, source_path=entry["root"]))
                except ValueError as exc:
                    record["omitted"].append(dict(source_path=template, reason=str(exc)))
        if record["supported_paths"]:
            record["status"] = "部分支持" if record["omitted"] else "文件扫描已实现"
    return policies, audit


def build_rules(days: int = 0) -> list[core.Rule]:
    from dataclasses import replace
    if days < 0:
        raise ValueError("扫描天数必须大于等于 0")
    _catalog.cache_clear()  # Newly installed package cache directories appear on rescan.
    policies, _ = _catalog(locations())
    return [replace(rule, days=days, modified_within=True) for rule in policies.values()]


def scope_root(scope_id: str) -> Path:
    policies, _ = _catalog(locations())
    if scope_id not in policies:
        raise ValueError("C 盘清理规则不存在，请重新扫描")
    return policies[scope_id].root


def authorize(path: Path, scope_id: str) -> bool:
    policies, _ = _catalog(locations())
    rule = policies.get(scope_id)
    if rule is None:
        return False
    path = core.canonical(path)
    if path == rule.root:
        return True
    if not core.inside(path, rule.root) or (not rule.recursive and path.parent != rule.root):
        return False
    return core.matches_rule(path, rule)


def coverage_report() -> dict:
    _, audit = _catalog(locations())
    enabled = sum(bool(i["supported_paths"]) for i in audit)
    return {"来源": manifest()["source"], "恢复项目数": len(audit), "恢复路径规则数": 197,
            "当前可生成文件规则的项目数": enabled,
            "解释范围": "恢复 XML 的路径规则经过逐项审核与缩小范围；不是完整原生 DSL 复现。仅系统盘当前用户；默认全部时间，可选择时间筛选；不执行动态表达式、不忽略未知排除条件。",
            "系统项目": "回收站、休眠、分页和组件存储在独立面板操作；不会进入文件全选/直接删除。",
            "逐项支持与未实现原因": audit}
