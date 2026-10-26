"""Category summaries over real scanned files, separate from Windows system actions.

The view can show category sizes while retaining the candidate snapshots needed by
the existing deletion checks. No scan, deletion or system action runs on consume.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import os

from cplus_cleaner.common import core


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


@dataclass
class CleanupCategory:
    key: str
    name: str
    group: str
    items: list[core.Candidate] = field(default_factory=list)
    selected: bool = False

    @property
    def bytes_count(self) -> int:
        return sum(item.size for item in self.items)

    @property
    def file_count(self) -> int:
        return len(self.items)

    @property
    def selectable(self) -> bool:
        return bool(self.items)


class CleanupSession:
    """Collect supported manifest categories without exposing individual files.

    Pass the same ``rules`` list used for scanning to keep the current rule
    coverage and the summaries aligned. An unimplemented recovered manifest item
    has no corresponding enabled rule and is not presented as a working category.
    Supported categories with no matches remain visible as zero and unselectable.
    """

    def __init__(self, rules: Iterable[core.Rule] | None = None,
                 manifest_items: Iterable[dict] | None = None):
        from cplus_cleaner.features.drive_cleanup import cdrive

        if rules is None:
            rules = cdrive.build_rules()
        if manifest_items is None:
            manifest_items = cdrive.manifest()["items"]
        enabled_names = {rule.name for rule in rules}
        self.categories: list[CleanupCategory] = []
        self._by_key: dict[str, CleanupCategory] = {}
        self._by_source: dict[str, CleanupCategory] = {}
        self._seen: set[str] = set()
        for entry in manifest_items:
            source = f"{entry['group']} / {entry['name']}"
            if source not in enabled_names:
                continue
            key = f"fq:{entry['index']}"
            if key in self._by_key or source in self._by_source:
                continue
            category = CleanupCategory(key, entry["name"], entry["group"])
            self.categories.append(category)
            self._by_key[key] = category
            self._by_source[source] = category

    def consume(self, items: Iterable[core.Candidate]) -> int:
        """Append a streamed batch; the first supported category owns each path.

        Partial batches remain in the model if scanning is later cancelled.
        Unknown category labels are excluded, not created from arbitrary input.
        """
        added = 0
        for item in items:
            if not isinstance(item, core.Candidate):
                raise TypeError("只接受扫描得到的普通文件候选；系统管理项目必须单独展示")
            category = self._by_source.get(item.category)
            if category is None:
                continue
            identity = _path_key(item.path)
            if identity in self._seen:
                continue
            self._seen.add(identity)
            category.items.append(item)
            added += 1
        return added

    @property
    def groups(self) -> dict[str, list[CleanupCategory]]:
        result: dict[str, list[CleanupCategory]] = {}
        for category in self.categories:
            result.setdefault(category.group, []).append(category)
        return result

    @property
    def group_totals(self) -> dict[str, dict]:
        result = {}
        for group, categories in self.groups.items():
            selectable = [category for category in categories if category.selectable]
            selected = [category for category in categories if category.selected]
            result[group] = dict(bytes_count=sum(category.bytes_count for category in categories),
                                 file_count=sum(category.file_count for category in categories),
                                 selected_bytes=sum(category.bytes_count for category in selected),
                                 selected_count=sum(category.file_count for category in selected),
                                 selected=bool(selectable) and all(category.selected for category in selectable))
        return result

    @property
    def total_bytes(self) -> int:
        return sum(category.bytes_count for category in self.categories)

    @property
    def total_files(self) -> int:
        return sum(category.file_count for category in self.categories)

    @property
    def selected_bytes(self) -> int:
        return sum(category.bytes_count for category in self.categories if category.selected)

    @property
    def selected_count(self) -> int:
        return sum(category.file_count for category in self.categories if category.selected)

    def selected_items(self) -> list[core.Candidate]:
        return [item for category in self.categories if category.selected for item in category.items]

    def set_selected(self, key: str, selected: bool) -> None:
        category = self._by_key[key]
        category.selected = bool(selected) and category.selectable

    def set_group_selected(self, group: str, selected: bool) -> None:
        for category in self.categories:
            if category.group == group:
                category.selected = bool(selected) and category.selectable

    def select_all(self, selected: bool = True) -> None:
        for category in self.categories:
            category.selected = bool(selected) and category.selectable

    def refresh_after_delete(self, result: dict | None = None) -> int:
        """Remove confirmed-missing paths and retain reported failures/unknowns.

        ``core.delete_files`` returns per-path errors rather than successful path
        lists, so a file is removed from summaries only after lstat confirms that
        it is gone. Permission errors and changed files retain their snapshots;
        the deletion engine will continue to validate them before a retry.
        """
        failed = {_path_key(error["path"]) for error in (result or {}).get("errors", [])
                  if isinstance(error, dict) and error.get("path")}
        removed = 0
        for category in self.categories:
            remaining = []
            for item in category.items:
                if _path_key(item.path) in failed:
                    remaining.append(item)
                    continue
                try:
                    os.lstat(item.path)
                except FileNotFoundError:
                    removed += 1
                    continue
                except OSError:
                    pass  # Unknown accessibility is not evidence of deletion.
                remaining.append(item)
            category.items[:] = remaining
            if not category.items:
                category.selected = False
        self._seen = {_path_key(item.path) for category in self.categories for item in category.items}
        return removed

    def apply_delete_result(self, result: dict) -> int:
        return self.refresh_after_delete(result)
