"""Original privacy categories, with record counts rather than disk usage."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

from cplus_cleaner.features.privacy import privacy_browser
from cplus_cleaner.features.privacy import privacy_windows

GROUPS = [('windows', 'Windows运行痕迹'), ('software', '软件使用痕迹'),
          ('browsers', '浏览器记录'), ('risk', '高危隐私')]
DEFINITIONS = [
    ('run', 'windows', 'Win+R 运行痕迹'),
    ('start_recent', 'windows', '开始菜单最近运行的程序'),
    ('recent_docs', 'windows', '最近访问的文件'),
    ('extensions', 'windows', '文件扩展名记录'),
    ('app_switched', 'windows', '最近运行的程序'),
    ('regedit', 'windows', '注册表最后一次位置'),
    ('thunder', 'software', '迅雷下载历史'),
    ('chrome_history', 'browsers', 'Chrome浏览历史记录'),
    ('chrome_downloads', 'browsers', 'Chrome下载记录'),
    ('edge_history', 'browsers', 'Edge浏览历史记录'),
    ('edge_downloads', 'browsers', 'Edge下载记录'),
    ('sensitive', 'risk', '文件敏感隐私'),
    ('shopping', 'risk', '网购痕迹'),
]


@dataclass
class Category:
    key: str
    group: str
    name: str
    records: list = field(default_factory=list)
    selected: bool = True
    diagnostics: list = field(default_factory=list)

    @property
    def view_only(self):
        return self.key == 'start_recent'

    @property
    def count(self):
        return len(self.records) if not self.view_only else 0

    @property
    def problems(self):
        return [d for d in self.diagnostics if d.get('status') in ('error', 'unsupported')]


class Session:
    def __init__(self):
        self.categories = {key: Category(key, group, name) for key, group, name in DEFINITIONS}
        self.scanned = False
        self.cancelled = False

    @property
    def total_count(self):
        return sum(c.count for c in self.categories.values())

    @property
    def selected_count(self):
        return sum(c.count for c in self.categories.values() if c.selected)

    @property
    def problems(self):
        return [problem for category in self.categories.values() for problem in category.problems]

    def group(self, key):
        return [c for c in self.categories.values() if c.group == key]

    def toggle(self, key):
        category = self.categories[key]
        if not category.view_only:
            category.selected = not category.selected

    def set_group(self, key, selected):
        for category in self.group(key):
            if not category.view_only:
                category.selected = selected

    def selected_records(self):
        return [item for c in self.categories.values() if c.selected and not c.view_only for item in c.records]


def scan(cancel=None, progress=None, *, browser_roots=None, registry=None, thunder_paths=None):
    session = Session()
    windows = privacy_windows.scan_registry(cancel, progress, registry=registry)
    thunder = privacy_windows.scan_thunder(cancel, progress, paths=thunder_paths)
    browsers = privacy_browser.scan(cancel, progress, roots=browser_roots)
    for result in (windows, thunder, browsers):
        for item in result['items']:
            if item['category'] in session.categories:
                session.categories[item['category']].records.append(item)
        for diagnostic in result['diagnostics']:
            category = session.categories.get(diagnostic['category'])
            if category:
                category.diagnostics.append(diagnostic)
            if diagnostic['category'] == 'recent_docs':
                session.categories['extensions'].diagnostics.append(diagnostic)
            if result is browsers and diagnostic.get('status') in ('error', 'unsupported'):
                session.categories['shopping' if diagnostic['category'].endswith('_history') else 'sensitive'].diagnostics.append(diagnostic)
    for kind, rows in [('shopping', browsers['shopping_groups']), ('sensitive', browsers['sensitive_groups'])]:
        for row in rows:
            # The display counts each native map key once. All underlying rows
            # are retained in memory for selective cleanup across browsers.
            members = row['members']
            identity = hashlib.sha256('|'.join(m['id'] for m in members).encode()).hexdigest()
            session.categories[kind].records.append(dict(id=kind+':'+identity, kind='browser_group',
                category=kind, members=members))
    session.scanned = True
    session.cancelled = any(r.get('cancelled') for r in (windows, thunder, browsers))
    return session


def clean(session, cancel=None, progress=None):
    selected = session.selected_records()
    groups = [item for item in selected if item['kind'] == 'registry_group']
    covered = {member['id'] for item in groups for member in item['members']}
    # Process captured extension groups once, then disjoint individual values.
    registry_items = groups + [item for item in selected if item['kind'] == 'registry' and item['id'] not in covered]
    thunder_items = [item for item in selected if item['kind'] == 'thunder']
    browser_items = [item for item in selected if item['kind'] in ('browser', 'browser_group')]
    cleaned, errors = set(), []
    for label, operation in [
        ('清理 Windows 运行痕迹', lambda: privacy_windows.clean_registry(registry_items, cancel)),
        ('清理迅雷下载历史', lambda: privacy_windows.clean_thunder(thunder_items, cancel)),
        ('清理浏览器记录', lambda: privacy_browser.clean(browser_items, cancel, progress)),
    ]:
        if cancel is not None and cancel.is_set():
            break
        if progress:
            progress(dict(message=label, completed=None, total=None))
        result = operation()
        cleaned.update(result['cleaned_ids'])
        errors.extend(result['errors'])
    # Category totals may overlap (e.g. shopping is a subset of browser history).
    # Reconcile all categories without counting/deleting a physical record twice.
    removed = 0
    for category in session.categories.values():
        retained = []
        for item in category.records:
            if item['id'] in cleaned:
                removed += 1
            elif item.get('members'):
                members = [m for m in item['members'] if m['id'] not in cleaned]
                if members:
                    retained.append(dict(item, members=members))
                else:
                    removed += 1
            else:
                retained.append(item)
        category.records = retained
    return dict(cleaned_count=removed, cleaned_physical_count=len(cleaned), errors=errors,
                cancelled=cancel is not None and cancel.is_set())
