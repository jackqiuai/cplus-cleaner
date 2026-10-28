"""CCCLEANER desktop: category summaries over the existing verified backend."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import threading
import queue
import json
from dataclasses import replace
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.localization.localized_tk import ttk, messagebox

from cplus_cleaner.common import core
from cplus_cleaner.features.drive_cleanup import cdrive
from cplus_cleaner.features.system import cdrive_system
from cplus_cleaner.application import preferences
from cplus_cleaner.localization import i18n
from cplus_cleaner.application import tray
from cplus_cleaner.ui.desktop_panels import OperationsPanel, SettingsPanel, FeedbackPanel
from cplus_cleaner.ui.donation_panel import DonationPanel
from cplus_cleaner.ui.app_menu import HeaderMenu
from cplus_cleaner.ui.ui_widgets import Checkbutton, check_images, MetricLabel, single_window, scroll_canvas
from cplus_cleaner.features.drive_cleanup.cleanup_model import CleanupSession
from cplus_cleaner.ui.ui_tasks import TaskRunner
from cplus_cleaner.features.system.system_actions_ui import SYSTEM_ACTIONS as SYSTEM_MAINTENANCE_ACTIONS, run_system_action as run_maintenance_action

WHITE = "#ffffff"
INK = "#263b40"
MUTED = "#85969b"
MINT = "#46d6a0"
PALE = "#ecfaf5"
LINE = "#edf2f2"
CORAL = "#ff6269"
ASSETS = core.APP_DIR / "assets"
FONT = "Microsoft YaHei UI"


def size_text(value):
    if not value:
        return "0KB"
    value = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:,.2f}".rstrip("0").rstrip(".") + unit
        value /= 1024


def rounded(canvas, x1, y1, x2, y2, radius=14, **options):
    points = [x1+radius, y1, x2-radius, y1, x2, y1, x2, y1+radius,
              x2, y2-radius, x2, y2, x2-radius, y2, x1+radius, y2,
              x1, y2, x1, y2-radius, x1, y1+radius, x1, y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=20, **options)


class PillButton(tk.Canvas):
    def __init__(self, parent, text, command, width=154, height=43, light=False, font=None):
        self.minimum_width = width
        super().__init__(parent, width=width, height=height, bg=WHITE, highlightthickness=0, cursor="hand2")
        self.caption, self.command, self.enabled, self.light = text, command, True, light
        self.text_font = font or (FONT, 12, "bold")
        self.bind("<Button-1>", lambda event: self.invoke())
        self.bind("<Enter>", lambda event: self.paint(True))
        self.bind("<Leave>", lambda event: self.paint())
        self.paint()

    def paint(self, hover=False):
        self.delete("all")
        from tkinter import font as tkfont
        needed = tkfont.Font(self, font=self.text_font).measure(i18n.tr(self.caption)) + 28
        self.configure(width=max(self.minimum_width, needed))
        width, height = int(self.cget("width")), int(self.cget("height"))
        fill = ("#36c890" if hover else MINT) if self.enabled else "#dce9e4"
        if self.light:
            fill = PALE if self.enabled else "#f2f5f4"
        rounded(self, 1, 1, width-1, height-1, height//2, fill=fill, outline="")
        self.create_text(width/2, height/2, text=self.caption, fill=INK if self.light else WHITE,
                         font=self.text_font)

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self.paint()

    def invoke(self):
        if self.enabled:
            return self.command()

    def _language_changed(self):
        self.paint()


class Images:
    def __init__(self, master):
        self.master, self.cache = master, {}
        self.hero_sources, self.hero_sizes = {}, {}
        try:
            self.mapping = json.loads((ASSETS / "category-icons.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.mapping = {}

    def get(self, name):
        if name in {"hero-clean.png", "hero-large.png"}:
            return self.illustration(name)
        if name not in self.cache:
            try:
                self.cache[name] = tk.PhotoImage(master=self.master, file=str(ASSETS / name))
            except (OSError, tk.TclError):
                self.cache[name] = None
        return self.cache[name]

    def illustration(self, name, width=300, height=330):
        """Fit transparent HD artwork with Tk; no runtime imaging dependency."""
        if name not in self.hero_sources:
            try:
                self.hero_sources[name] = tk.PhotoImage(master=self.master, file=str(ASSETS / name))
            except (OSError, tk.TclError):
                self.hero_sources[name] = None
        original = self.hero_sources[name]
        if original is None:
            return None
        width, height = max(1, int(width)), max(1, int(height))
        factor = max(1, (original.width()+width-1)//width, (original.height()+height-1)//height)
        key = name, factor
        if key not in self.hero_sizes:
            from cplus_cleaner.common.image_opacity import display_png
            scaled = original.subsample(factor) if factor > 1 else original
            data = scaled.tk.call(str(scaled), "data", "-format", "png")
            self.hero_sizes[key] = tk.PhotoImage(master=self.master, data=display_png(data))
        return self.hero_sizes[key]

    def navigation(self, key, active=False):
        if key == "system":
            return self.get("nav-system-active.png" if active else "nav-system.png")
        if key == "chat":
            key = "wechat"  # Preserve the recovered WeChat navigation assets.
        section = "navigationActive" if active else "navigation"
        filename = self.mapping.get(section, {}).get(key, f"nav-{key}{'-active' if active else ''}.png")
        return self.get(filename)

    def system(self, name):
        filename = self.mapping.get("system", {}).get(name)
        return self.get(filename) if filename else None

    def icon(self, name, group=None):
        source = f"{group} / {name}"
        filename = self.mapping.get("categories", {}).get(source)
        if filename:
            found = self.get(filename)
            if found:
                return found
        pairs = [("QQ", "qq.png"), ("Chrome", "chrome.png"), ("Edge", "edge.png"),
                 ("WPS", "wps.png"), ("日志", "logs.png"), ("临时", "temp.png"),
                 ("报告", "system.png"), ("回收", "recycle.png"), ("缓存", "cache.png")]
        for keyword, filename in pairs:
            if keyword.lower() in name.lower():
                found = self.get("icon-" + filename)
                if found:
                    return found
        return self.get("icon-cache.png")


def text_button(parent, text, command, color=MUTED):
    return tk.Button(parent, text=text, command=command, bg=WHITE, fg=color, activebackground=PALE,
                     activeforeground=INK, relief="flat", bd=0, cursor="hand2", font=(FONT, 10), padx=8, pady=4)


def disk_snapshot():
    root = cdrive.locations().drive
    usage = shutil.disk_usage(root)
    return {"root": str(root), "total": usage.total, "used": usage.used, "free": usage.free}


def disk_name(info=None):
    root = (info or {}).get("root") or os.environ.get("SystemRoot", r"C:\Windows")
    return Path(root).drive.rstrip(":").upper() + "盘"


class DiskBar(tk.Frame):
    def __init__(self, parent, title=None, wide=320, color=CORAL):
        super().__init__(parent, bg=WHITE)
        self.dynamic_title = title is None
        top = tk.Frame(self, bg=WHITE)
        top.pack(fill="x")
        self.title = tk.Label(top, text=title if title is not None else f"{disk_name()}空间",
                              bg=WHITE, fg=MUTED, font=(FONT, 10))
        self.title.pack(side="left")
        self.amount = tk.Label(top, bg=WHITE, fg=color, font=(FONT, 10, "bold"))
        self.amount.pack(side="right")
        self.bar = tk.Canvas(self, width=wide, height=10, highlightthickness=0, bg=WHITE)
        self.bar.pack(fill="x", pady=(7, 0))
        self.color, self.info = color, None
        self.bar.bind("<Configure>", lambda event: self.paint())

    def update_info(self, info):
        self.info = info
        if self.dynamic_title:
            self.title.configure(text=f"{disk_name(info)}空间")
        self.amount.configure(text=f"{size_text(info['used'])} / {size_text(info['total'])}")
        self.paint()

    def paint(self):
        self.bar.delete("all")
        width = max(self.bar.winfo_width(), 100)
        rounded(self.bar, 0, 1, width, 8, 4, fill="#e9eded", outline="")
        if self.info and self.info["total"]:
            rounded(self.bar, 0, 1, max(7, width*self.info["used"]/self.info["total"]), 8, 4, fill=self.color, outline="")


class CategoryCard(tk.Canvas):
    def __init__(self, parent, page, category):
        super().__init__(parent, width=120, height=126, highlightthickness=0, bg=WHITE, cursor="hand2")
        self.page, self.category = page, category
        self.bind("<Button-1>", lambda event: self.toggle())
        self.bind("<Enter>", lambda event: self.page.app.status.set(f"{category.name} · {category.file_count:,} 项 · {size_text(category.bytes_count)}"))
        self.paint()

    def toggle(self):
        if not self.page.app.busy:
            self.page.session.set_selected(self.category.key, not self.category.selected)
            self.page.update_totals()

    def paint(self):
        self.delete("all")
        category = self.category
        image = self.page.app.images.icon(category.name, category.group)
        if image:
            self.create_image(60, 32, image=image)
        else:
            rounded(self, 38, 10, 82, 54, 10, fill="#e6f7f2", outline="")
            self.create_text(60, 32, text="▤", fill=MINT, font=(FONT, 25))
        self.create_image(78, 41, anchor="nw", image=check_images(self)["on" if category.selected else "off"], tags="check")
        self.create_text(60, 76, text=category.name, width=116, fill=INK if category.selectable else MUTED,
                         font=(FONT, 9), justify="center")
        rounded(self, 23, 100, 97, 120, 10, fill=CORAL if category.bytes_count else MINT, outline="")
        self.create_text(60, 110, text=size_text(category.bytes_count), fill=WHITE, font=(FONT, 9, "bold"))


class VirtualMemoryCard(tk.Canvas):
    """Display an observed system file; never supply it to ordinary deletion."""
    def __init__(self, parent, page, item):
        super().__init__(parent, width=146, height=134, highlightthickness=0, bg=WHITE, cursor="hand2")
        self.page, self.item = page, item
        self.bind("<Button-1>", lambda event: self.invoke())
        self.bind("<Enter>", lambda event: page.app.status.set("系统虚拟内存由 Windows 管理；点击打开设置，调整后需重启。"))
        self.paint()

    def invoke(self):
        self.page.run_system_action("pagefile_settings")

    def paint(self):
        self.delete("all")
        icon = self.page.app.images.system("virtual_memory")
        if icon:
            self.create_image(73, 27, image=icon)
        else:
            rounded(self, 51, 7, 95, 50, 10, fill=PALE, outline="")
            self.create_text(73, 28, text="▦", fill=MINT, font=(FONT, 25))
        self.create_text(73, 64, text="系统虚拟内存", fill=INK, font=(FONT, 9))
        size = self.item.get("size")
        rounded(self, 29, 79, 117, 101, 10, fill=CORAL if size else "#dbe8e3", outline="")
        self.create_text(73, 90, text="未知" if size is None else size_text(size), fill=WHITE, font=(FONT, 10, "bold"))
        self.create_text(73, 119, text="虚拟内存设置  ›", fill="#3ab28e", font=(FONT, 9))


class CategoryPage(tk.Frame):
    SYSTEM_ACTIONS = SYSTEM_MAINTENANCE_ACTIONS

    def __init__(self, parent, application, mode="cdrive"):
        super().__init__(parent, bg=WHITE)
        self.app, self.mode = application, mode
        self.subject = {"cdrive": "C盘", "wechat": "微信", "qq": "QQ", "privacy": "隐私痕迹"}[mode]
        self.session = None
        self.default_selections = frozenset()
        self.system_items, self.scan_details, self.cards, self.groups = [], [], {}, {}
        self.scanning, self._paint_pending, self._columns = False, None, 0
        self.header = tk.Frame(self, bg=WHITE)
        self.header.pack(fill="x", padx=34, pady=(27, 0))
        actions = tk.Frame(self.header, bg=WHITE)
        actions.pack(side="right", anchor="n")
        self.rescan_button = (PillButton(actions, "扫描", self.scan, width=150) if mode == "cdrive"
                              else text_button(actions, "扫描", self.scan))
        self.scan_button = PillButton(actions, "开始扫描", self.scan, width=150)
        self.scan_button.pack(side="right")
        self.clean_button = PillButton(actions, "立即清理", self.clean, width=150)
        self.heading = tk.StringVar(value="垃圾清理，释放 C 盘空间" if mode == "cdrive" else f"{self.subject}专清，按类型整理空间")
        self.subtitle = tk.StringVar(value="全面扫描，按类型整理系统盘中的缓存、临时文件与日志" if mode == "cdrive" else
                                     "整理最近访问记录；清理 Jump List 可能重置固定项" if mode == "privacy" else
                                     "选择聊天数据目录，按图片、视频、附件等类型汇总")
        MetricLabel(self.header, textvariable=self.heading, bg=WHITE, fg=INK, font=(FONT, 20, "bold")).pack(anchor="w")
        MetricLabel(self, textvariable=self.subtitle, bg=WHITE, fg=MUTED, font=(FONT, 10)).pack(anchor="w", padx=35, pady=(12, 19))
        self.options = tk.Frame(self, bg=WHITE)
        self.options.pack(fill="x", padx=35, pady=(0, 6))
        self.days = tk.StringVar(value="0")
        self.range_label = tk.StringVar(value="全部时间")
        tk.Label(self.options, text="扫描范围", bg=WHITE, fg=MUTED, font=(FONT, 9)).pack(side="left")
        ranges = {"全部时间": "0", "7天内": "7", "30天内": "30", "180天内": "180"}
        self.age_combo = ttk.Combobox(self.options, values=tuple(ranges), textvariable=self.range_label, width=10, state="readonly")
        self.age_combo.pack(side="left", padx=(8, 3))
        self.age_combo.bind("<<ComboboxSelected>>", lambda event: self.days.set(ranges[self.range_label.get()]))
        self.days.trace_add("write", lambda *args: self.range_label.set("全部时间" if self.days.get() == "0" else self.days.get() + "天内"))
        self.summary_controls = tk.Frame(self.options, bg=WHITE)
        text_button(self.summary_controls, "全选类型", lambda: self.select_all(True)).pack(side="left")
        text_button(self.summary_controls, "全不选", lambda: self.select_all(False)).pack(side="left")
        self.scan_options = {}
        if mode == "cdrive":
            presets = tk.Frame(self, bg=WHITE)
            presets.pack(fill="x", padx=35, pady=(3, 4))
            for index, (title, checked) in enumerate(cdrive.SCAN_OPTIONS):
                value = tk.BooleanVar(value=checked)
                self.scan_options[title] = value
                Checkbutton(presets, text=title, variable=value, bg=WHITE, fg=INK,
                               activebackground=WHITE, selectcolor=PALE, font=(FONT, 9),
                               highlightthickness=0, bd=0, cursor="hand2").grid(row=0, column=index, padx=(0, 12))
            text_button(presets, "恢复默认", self.restore_defaults).grid(row=0, column=len(cdrive.SCAN_OPTIONS))
        tk.Frame(self, bg=LINE, height=1).pack(fill="x", padx=35, pady=(7, 0))
        self.folder = tk.StringVar(value=str(Path.home() / ("Documents/WeChat Files" if mode == "wechat" else "Documents/Tencent Files")))
        if mode in ("wechat", "qq"):
            from cplus_cleaner.localization.localized_tk import filedialog
            location = tk.Frame(self, bg=WHITE)
            location.pack(fill="x", padx=35, pady=(10, 0))
            tk.Label(location, text="数据目录", bg=WHITE, fg=MUTED, font=(FONT, 9)).pack(side="left")
            ttk.Entry(location, textvariable=self.folder).pack(side="left", fill="x", expand=True, padx=8)
            def choose():
                value = filedialog.askdirectory(parent=self, title="选择聊天数据目录")
                if value:
                    self.folder.set(value)
            text_button(location, "选择目录", choose).pack(side="right")
        self.stage = tk.Frame(self, bg=WHITE)
        self.stage.pack(fill="both", expand=True)
        self.idle = tk.Frame(self.stage, bg=WHITE)
        self.idle.pack(fill="both", expand=True)
        intro = tk.Frame(self.idle, bg=WHITE)
        intro.pack(side="left", fill="both", expand=True, padx=(38, 0), pady=(38, 20))
        tk.Label(intro, text=f"全面扫描，按类型整理{self.subject}内容", bg=WHITE, fg="#698c8c", font=(FONT, 12)).pack(anchor="w", pady=(0, 22))
        types = tk.Frame(intro, bg=WHITE)
        types.pack(anchor="w")
        type_names = (() if mode == "cdrive" else
                      ("最近访问", "自动 Jump List", "自定义 Jump List") if mode == "privacy" else
                      ("聊天图片", "聊天视频", "接收文件", "缓存文件", "表情缓存", "聊天音频"))
        for index, title in enumerate(type_names):
            tk.Label(types, text="▤  " + title, fg=MUTED, bg=WHITE, font=(FONT, 10), padx=0, pady=10).grid(row=index//3, column=index%3, sticky="w", padx=(0, 25))
        self.disk = DiskBar(intro, wide=350)
        self.disk.pack(fill="x", pady=(48, 13), padx=(0, 15))
        tk.Label(intro, text="扫描结果按类型汇总，清理前由你选择", fg=MUTED, bg=WHITE, font=(FONT, 9)).pack(anchor="w", pady=(4, 0))
        art = tk.Canvas(self.idle, width=320, bg=WHITE, highlightthickness=0)
        self.idle_art = art
        art.pack(side="right", fill="y", padx=(0, 8))
        hero = self.app.images.get("hero-clean.png")
        if hero:
            picture = art.create_image(160, 210, image=hero)
            def fit_art(event):
                if event.width < 40 or event.height < 40:
                    return
                image = self.app.images.illustration("hero-clean.png", min(300, event.width-16),
                                                     min(330, event.height-16))
                art.itemconfigure(picture, image=image)
                art.coords(picture, event.width/2, event.height/2)
            art.bind("<Configure>", fit_art)
        else:
            art.create_text(160, 210, text="◇", font=(FONT, 120), fill=PALE)
        self.results = tk.Frame(self.stage, bg=WHITE)
        self.canvas = tk.Canvas(self.results, bg=WHITE, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.results, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y", padx=(0, 14), pady=15)
        self.canvas.pack(side="left", fill="both", expand=True, padx=(30, 5), pady=(10, 0))
        self.inner = tk.Frame(self.canvas, bg=WHITE)
        self.inner_window = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.inner.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self.resize_results)
        self.bind_all("<MouseWheel>", self.mousewheel, add="+")
        self.note = tk.StringVar(value="扫描只读取文件信息；清理所选类型将永久删除对应文件。")
        tk.Label(self, textvariable=self.note, fg=MUTED, bg=WHITE, font=(FONT, 9), wraplength=780, justify="left").pack(anchor="w", padx=35, pady=(9, 15))

    def refresh(self):
        try:
            self.disk.update_info(disk_snapshot())
        except OSError:
            pass

    def mousewheel(self, event):
        if self.results.winfo_ismapped():
            return scroll_canvas(self.canvas, self.inner, event.delta)

    def resize_results(self, event):
        self.canvas.itemconfigure(self.inner_window, width=event.width)
        columns = max(3, event.width//128)
        if columns != self._columns:
            self._columns = columns
            for group in self.groups.values():
                for i, card in enumerate(group["cards"]):
                    card.grid(row=i//columns, column=i%columns, padx=3, pady=5, sticky="n")

    def prepare(self, payload):
        rules, system_items = payload[:2]
        self.session = CleanupSession(rules, manifest_items=payload[2] if len(payload) > 2 else None)
        defaults = cdrive.default_selected_categories() if self.mode == "cdrive" else frozenset()
        self.default_selections = frozenset(c.key for c in self.session.categories if f"{c.group} / {c.name}" in defaults)
        self.system_items = list(system_items)
        self.cards, self.groups = {}, {}
        self.virtual_memory_card = None
        for child in self.inner.winfo_children():
            child.destroy()
        self.idle.pack_forget()
        self.results.pack(fill="both", expand=True)
        self.scan_button.pack_forget()
        self.rescan_button.pack(side="left", padx=(0, 8))
        self.clean_button.pack(side="right")
        self.clean_button.set_enabled(False)
        self.summary_controls.pack(side="left", padx=(15, 0))
        if self.mode == "cdrive":
            self.build_virtual_memory_section()
        order = ("临时文件", "缓存文件", "应用程序", "系统相关", "备份文件")
        groups = self.session.groups
        for name in [x for x in order if x in groups] + [x for x in groups if x not in order]:
            frame = tk.Frame(self.inner, bg=WHITE)
            frame.pack(fill="x", pady=(14, 6))
            row = tk.Frame(frame, bg=WHITE)
            row.pack(fill="x", padx=(3, 20))
            toggle = text_button(row, name, lambda value=name: self.toggle_group(value), INK)
            toggle.configure(image=check_images(self)["off"], compound="left")
            toggle.pack(side="left")
            amount = MetricLabel(row, bg=WHITE, fg=MUTED, font=(FONT, 9))
            amount.pack(side="right")
            collapse = text_button(row, "⌄", lambda value=name: self.collapse_group(value))
            collapse.pack(side="right", padx=(0, 8))
            body = tk.Frame(frame, bg=WHITE)
            body.pack(fill="x", pady=(8, 0))
            cards = []
            for index, category in enumerate(groups[name]):
                card = CategoryCard(body, self, category)
                columns = self._columns or 5
                card.grid(row=index//columns, column=index%columns, padx=3, pady=5, sticky="n")
                self.cards[category.key] = card
                cards.append(card)
            self.groups[name] = dict(cards=cards, toggle=toggle, amount=amount,
                                     body=body, collapse=collapse, collapsed=False)
        self.canvas.yview_moveto(0)
        self.update_totals()

    def build_virtual_memory_section(self):
        item = next((i for i in self.system_items if i.get("id") == "pagefile"), None)
        if item is None:
            return
        frame = tk.Frame(self.inner, bg=WHITE)
        frame.pack(fill="x", padx=5, pady=(16, 7))
        header = tk.Frame(frame, bg=WHITE)
        header.pack(fill="x", padx=(3, 20))
        tk.Label(header, text="虚拟内存", bg=WHITE, fg=INK, font=(FONT, 11)).pack(side="left")
        tk.Label(header, text="调整后需重启生效", bg=WHITE, fg=CORAL, font=(FONT, 9)).pack(side="left", padx=8)
        self.virtual_memory_amount = tk.Label(header, bg=WHITE, fg=MUTED, font=(FONT, 9))
        self.virtual_memory_amount.pack(side="right")
        self.virtual_memory_card = VirtualMemoryCard(frame, self, item)
        self.virtual_memory_card.pack(anchor="w", padx=8, pady=(10, 0))
        self.virtual_memory_amount.configure(text=f"占用 {'未知' if item.get('size') is None else size_text(item['size'])}")
        tk.Label(frame, text="系统管理项目，单独调整，不计入普通缓存可清理总数。", bg=WHITE, fg=MUTED,
                 font=(FONT, 9)).pack(anchor="w", padx=8, pady=(3, 7))

    def set_system_items(self, items):
        self.system_items = list(items)
        if getattr(self, "virtual_memory_card", None):
            item = next((i for i in items if i.get("id") == "pagefile"), None)
            if item:
                self.virtual_memory_card.item = item
                self.virtual_memory_card.paint()
                self.virtual_memory_amount.configure(text=f"占用 {'未知' if item.get('size') is None else size_text(item['size'])}")
        if self.session:
            self.update_totals()

    def refresh_system(self):
        self.app.start("读取系统项目", cdrive_system.read_system_items, self.set_system_items)

    run_system_action = run_maintenance_action

    def scan_inputs(self, days, folder, enabled_options=None):
        if self.mode == "cdrive":
            rules = cdrive.build_rules(days=days)
            if enabled_options is not None:
                rules = [rule for rule in rules if cdrive.scan_option(rule.group, rule.name) in enabled_options]
            return rules, cdrive_system.read_system_items(), None
        if self.mode == "privacy":
            source = [r for r in core.default_rules(days=0) if r.group == "隐私痕迹"]
        else:
            core.check_path(folder)
            if not folder.is_dir():
                raise ValueError("聊天数据目录不存在，请先选择实际账户或聊天数据目录")
            source = core.chat_rules(folder, days)
            if not source:
                raise ValueError("没有找到支持的聊天附件目录，请重新选择账户目录")
        rules, entries, identities = [], [], {}
        for rule in source:
            if self.mode == "privacy":
                name, group = rule.name, "隐私痕迹"
            else:
                parts = [p.lower() for p in rule.root.parts]
                leaf = parts[-1]
                name = ("聊天图片" if leaf in ("image", "pic", "group2", "c2c") else
                        "聊天视频" if leaf == "video" else "聊天音频" if leaf == "audio" else
                        "接收文件" if leaf in ("file", "filerecv") else "表情缓存" if leaf == "customemotion" else
                        "临时文件" if leaf in ("temp", "tempfromphone") else "缓存文件")
                group = self.subject + "专清"
            identity = (group, name)
            if identity not in identities:
                identities[identity] = len(identities)
                entries.append(dict(index=identities[identity], name=name, group=group))
            rules.append(replace(rule, name=f"{group} / {name}", group=group))
        return rules, [], entries

    def scan(self):
        if self.app.busy:
            return
        try:
            days = int(self.days.get())
            if days < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("扫描范围", "请输入大于等于 0 的天数")
            return
        self.scanning = True
        self.scan_details = []
        self.heading.set(f"正在扫描{self.subject}…")
        self.subtitle.set("正在归类并计算占用空间，请稍候")
        self.note.set("扫描中，只读取文件信息。可点击底部“取消任务”保留已找到的类型。")
        folder = Path(self.folder.get())
        enabled_options = frozenset(name for name, value in self.scan_options.items() if value.get()) if self.mode == "cdrive" else None
        def work():
            rules, system_items, manifest_items = self.scan_inputs(days, folder, enabled_options)
            self.app.events.put(("batch", (self.prepare, (rules, system_items, manifest_items))))
            return core.scan_rules(rules, self.app.cancel,
                                   lambda text: self.app.report_progress("正在扫描清理类型…"),
                                   diagnostics=self.scan_details,
                                   on_batch=lambda items: self.app.events.put(("batch", (self.consume, items))))
        def done(result):
            self.scanning = False
            self.flush_paint()
            errors = sum(d["errors"] for d in self.scan_details)
            selection_note = (f"已默认勾选 {sum(c.selected for c in self.session.categories)} 个常规类型，可自行调整。"
                              if self.mode == "cdrive" else "默认未勾选。")
            self.note.set(("扫描已取消，显示已完成部分。" if self.app.cancel.is_set() else "扫描完成。") +
                          f"发现 {sum(c.selectable for c in self.session.categories)} 种可清理内容，{selection_note}" +
                          (f" {errors} 项无法读取，已跳过。" if errors else ""))
            self.update_totals()
            self.app.home_page.refresh()
        def failed(error):
            self.scanning = False
            self.heading.set("扫描未完成")
            self.note.set("请重试，或调整扫描范围。")
        self.app.start("正在扫描清理类型…", work, done, failed)

    def consume(self, items):
        self.session.consume(items)
        if self.scanning:
            self.apply_defaults()
        if self._paint_pending is None:
            self._paint_pending = self.after(150, self.flush_paint)

    def flush_paint(self):
        if self._paint_pending is not None:
            self.after_cancel(self._paint_pending)
            self._paint_pending = None
        self.update_totals()

    def update_totals(self):
        if not self.session:
            return
        total = size_text(self.session.total_bytes)
        self.app.set_nav_metric(self.mode, self.session.total_bytes)
        selected = size_text(self.session.selected_bytes)
        self.heading.set(f"{'正在扫描' if self.scanning else '发现可释放'} {total}")
        self.subtitle.set(f"已选 {selected}    ·    {sum(c.selected for c in self.session.categories)} 个类型    ·    按类型选择后清理对应内容")
        pagefile = next((i for i in self.system_items if i.get("id") == "pagefile"), None)
        if pagefile and self.mode == "cdrive":
            amount = "未知" if pagefile.get("size") is None else size_text(pagefile["size"])
            self.subtitle.set(f"已选 {selected}    ·    虚拟内存占用 {amount}（单独管理）")
        totals = self.session.group_totals
        for name, group in self.groups.items():
            data = totals[name]
            group["amount"].configure(text=f"共 {size_text(data['bytes_count'])}    已选 {size_text(data['selected_bytes'])}")
            state = "on" if data["selected"] else "mixed" if any(c.selected for c in self.session.groups[name]) else "off"
            group["toggle"].configure(image=check_images(self)[state])
        for card in self.cards.values():
            card.paint()
        self.clean_button.set_enabled(bool(self.session.selected_count) and not self.app.busy)

    def toggle_group(self, name):
        if self.app.busy:
            return
        self.session.set_group_selected(name, not self.session.group_totals[name]["selected"])
        self.update_totals()

    def apply_defaults(self):
        for category in self.session.categories:
            if category.key in self.default_selections and category.selectable:
                self.session.set_selected(category.key, True)

    def restore_defaults(self):
        if self.app.busy:
            return
        self.days.set("0")
        for name, checked in cdrive.SCAN_OPTIONS:
            self.scan_options[name].set(checked)
        if self.session:
            self.session.select_all(False)
            self.apply_defaults()
            self.update_totals()
        self.app.status.set("已恢复默认勾选；扫描范围修改在重新扫描后生效")

    def collapse_group(self, name):
        group = self.groups[name]
        group["collapsed"] = not group["collapsed"]
        if group["collapsed"]:
            group["body"].pack_forget()
        else:
            group["body"].pack(fill="x", pady=(8, 0))
        group["collapse"].configure(text="›" if group["collapsed"] else "⌄")

    def select_all(self, value):
        if self.session and not self.app.busy:
            self.session.select_all(value)
            self.update_totals()

    def clean(self):
        if self.app.busy or not self.session:
            return
        items = self.session.selected_items()
        if not items:
            self.app.status.set("先勾选要清理的类型")
            return
        amount = self.session.selected_bytes
        self.clean_button.set_enabled(False)
        def work():
            return core.delete_files(items, self.app.cancel, lambda path: self.app.report_progress("正在清理所选类型…"))
        def done(result):
            self.session.apply_delete_result(result)
            self.update_totals()
            from cplus_cleaner.features.home.home_results import refresh_cleanup
            refresh_cleanup(self.app)
            self.note.set(f"已清理 {size_text(result['bytes'])}，失败/跳过 {len(result['errors'])} 项。" +
                          ("任务已取消。" if result.get("cancelled") else "可重新扫描查看剩余内容。"))
            self.app.home_page.refresh()
            self.app.status.set(f"本次清理完成 · 计划 {size_text(amount)} · 已处理 {size_text(result['bytes'])}")
        def failed(error):
            self.update_totals()
            self.note.set("清理未完成，请检查错误信息后重试。")
        self.app.start("清理所选类型…", work, done, failed)



class DesktopApplication(tk.Tk):
    report_progress = TaskRunner.report_progress

    NAV = [("home", "首页", "⌂"), ("cdrive", "C盘清理", "▤"), ("chat", "聊天清理", "♧"),
           ("large", "大文件搬家", "▣"), ("migrate", "软件搬家", "◇"),
           ("compress", "软件压缩", "▧"), ("duplicates", "重复文件清理", "▥"),
           ("privacy", "隐私清理", "♧"), ("uninstall", "软件卸载", "▱"), ("popup", "弹窗拦截", "▱"),
           ("system", "系统管理", "⚙")]

    def __init__(self, background=False):
        self.settings = preferences.load()
        i18n.set_language(self.settings.get('language', ''))
        super().__init__()
        self.title("CPlus Cleaner · C盘清理助手")
        self.geometry("1160x760")
        self.minsize(980, 650)
        self.configure(bg=WHITE)
        self.busy, self.cancel = False, threading.Event()
        self.events, self.last_progress = queue.Queue(), 0
        self.tray_events, self.tray_icon = queue.Queue(), None
        self._closing, self._schedule_timer = False, None
        self._popup_resume = None
        self._settings_window = self._feedback_window = self._operations_window = None
        self._donation_window = None
        try:
            self.settings["autostart"] = preferences.read_autostart()
        except OSError:
            pass
        self.scheduler = preferences.DailyScheduler(self.settings)
        self.previous_tool = "home"
        self.status = tk.StringVar(value="就绪")
        self.images = Images(self)
        icon = self.images.get("app-icon.png")
        if icon:
            self.iconphoto(True, icon)
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background=WHITE)
        style.configure("TLabel", background=WHITE, foreground=INK, font=(FONT, 10))
        style.configure("TButton", padding=(10, 5), font=(FONT, 10))
        style.configure("Treeview", rowheight=27, font=(FONT, 9))
        style.configure("Desk.TNotebook", background=WHITE, borderwidth=0, tabmargins=0,
                        lightcolor=WHITE, darkcolor=WHITE, bordercolor=WHITE)
        style.layout("Desk.TNotebook.Tab", [])
        style.configure("Mint.Horizontal.TProgressbar", background=MINT, troughcolor=WHITE, borderwidth=0, thickness=3)
        self.sidebar = tk.Frame(self, bg=WHITE, width=214, highlightbackground=LINE, highlightthickness=1)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.update_language_layout()
        brand = tk.Canvas(self.sidebar, width=60, height=63, highlightthickness=0, bg=WHITE)
        brand.pack(pady=(14, 3))
        brand.create_image(30, 26, image=self.images.get("brand/sidebar.png"))
        tk.Label(self.sidebar, text="CPlus Cleaner", font=("Segoe UI", 12, "bold"), bg=WHITE, fg=INK).pack()
        tk.Label(self.sidebar, text="C盘清理助手", font=(FONT, 9), bg=WHITE, fg=MUTED).pack(pady=(3, 12))
        self.nav_widgets, self.nav_icons, self.nav_badges, self.nav_metrics = {}, {}, {}, {}
        for key, name, glyph in self.NAV:
            row = tk.Frame(self.sidebar, bg=WHITE, height=41, cursor="hand2")
            row.pack(fill="x")
            row.pack_propagate(False)
            marker = tk.Frame(row, width=3, bg=WHITE)
            marker.pack(side="left", fill="y")
            normal, active_icon = self.images.navigation(key), self.images.navigation(key, active=True)
            icon_label = tk.Label(row, image=normal or "", text="" if normal else glyph, bg=WHITE, fg="#719397", font=(FONT, 14))
            icon_label.pack(side="left", padx=(18, 8))
            label = tk.Label(row, text=name, anchor="w", bg=WHITE, fg="#719397", font=(FONT, 10), padx=0)
            label.pack(side="left", fill="both", expand=True)
            badge = tk.Label(row, bg=CORAL, fg=WHITE, bd=0, padx=3, pady=0,
                             font=("Segoe UI", 8, "bold"), cursor="hand2")
            for widget in (row, marker, label, icon_label, badge):
                widget.bind("<Button-1>", lambda event, value=key: self.navigate(value))
            self.nav_widgets[key] = row, marker, label
            self.nav_icons[key] = icon_label, normal, active_icon
            self.nav_badges[key] = badge
        right = tk.Frame(self, bg=WHITE)
        right.pack(side="left", fill="both", expand=True)
        top = tk.Frame(right, bg=WHITE, height=25)
        top.pack(fill="x", padx=23, pady=(8, 0))
        self.menu_button = tk.Canvas(top, width=32, height=28, highlightthickness=0, bg=WHITE, cursor="hand2", takefocus=True)
        for y in (7, 14, 21):
            self.menu_button.create_line(7, y, 25, y, fill="#719397", width=2)
        self.menu_button.pack(side="right", padx=(8, 0))
        self.more_menu = HeaderMenu(self, self.menu_button, [
            ("设置", self.open_settings),
            ("问题反馈", self.open_feedback),
            ("捐赠", self.open_donation),
            (f"版本号  v{preferences.VERSION}", None),
            ("退出软件", self.quit_app),
        ])
        for event in ("<Button-1>", "<Return>", "<space>"):
            self.menu_button.bind(event, self.open_menu)
        text_button(top, "操作记录", self.open_logs).pack(side="right")
        self.notebook = ttk.Notebook(right, style="Desk.TNotebook")
        self.notebook.pack(fill="both", expand=True)
        self.cdrive_page = CategoryPage(self.notebook, self)
        from cplus_cleaner.features.home.home_ui import HomePage
        self.home_page = HomePage(self.notebook, self)
        self.views = {"home": self.home_page, "cdrive": self.cdrive_page}
        for key, page in self.views.items():
            self.notebook.add(page, text=key)
        # Tool pages retain their existing backends; they are created when opened.
        self.progress_container = tk.Frame(right, height=3, bg=WHITE)
        self.progress = ttk.Progressbar(self.progress_container, mode="indeterminate", style="Mint.Horizontal.TProgressbar")
        self.progress.place(x=0, y=0, relwidth=1, relheight=1)
        self.footer = footer = tk.Frame(right, bg=WHITE)
        footer.pack(fill="x", padx=22, pady=(5, 8))
        self.status_label = tk.Label(footer, textvariable=self.status, fg=MUTED, bg=WHITE, anchor="w", font=(FONT, 8))
        self.status_label.pack(side="left", fill="x", expand=True)
        text_button(footer, "取消任务", self.cancel.set).pack(side="right")
        # Reserve the bottom controls before the notebook consumes remaining space.
        footer.pack_configure(side="bottom", before=self.notebook)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self.poll)
        self.navigate("home")
        self._popup_resume = self.after_idle(self.resume_popup_blocking)
        self._schedule_timer = self.after(1000, self.schedule_tick)
        if background:
            self.after_idle(self.hide_to_tray)

    def set_nav_metric(self, key, value, unit="bytes"):
        if key not in self.nav_badges or key == "home":
            return
        badge = self.nav_badges[key]
        if value is None:
            self.nav_metrics.pop(key, None)
            badge.pack_forget()
            return
        amount = max(0, int(value))
        text = size_text(amount) if unit == "bytes" else f"{amount}{unit}"
        metric = dict(value=amount, unit=unit, text=text)
        if self.nav_metrics.get(key) == metric:
            return
        self.nav_metrics[key] = metric
        badge.configure(text=text)
        if not badge.winfo_manager():
            badge.pack(side="right", padx=(2, 6), before=self.nav_widgets[key][2])

    def start(self, label, work, done, failed=None):
        if not self.busy:
            key = getattr(self, "current_page", "home")
            if key != "home":
                self.home_page.invalidate(key)
            if label != "首页一键扫描":
                self.progress_container.pack(fill="x", side="bottom", before=self.notebook)
        return TaskRunner.start(self, label, work, done, failed)

    def poll(self):
        was_busy = self.busy
        TaskRunner.poll(self)
        try:
            while True:
                _, action = self.tray_events.get_nowait()
                if action == "exit":
                    self.restore_window()
                    self.quit_app()
                    if self._closing:
                        return
                else:
                    self.restore_window()
                    if action == "settings":
                        self.open_settings()
        except queue.Empty:
            pass
        if not self.busy:
            self.progress_container.pack_forget()
            if was_busy and self.current_page in {"migrate", "compress", "chat", "system"}:
                self.views[self.current_page].update_summary()

    def open_menu(self, event=None):
        return self.more_menu.toggle(event)

    def open_logs(self):
        return self.open_panel_window("operations")

    def open_settings(self):
        return self.open_panel_window("settings")

    def open_feedback(self):
        return self.open_panel_window("feedback")

    def open_donation(self):
        return self.open_panel_window("donation")

    def open_panel_window(self, key):
        title, factory = {"settings": ("设置", SettingsPanel),
                          "feedback": ("问题反馈", FeedbackPanel),
                          "donation": ("捐赠", DonationPanel),
                          "operations": ("操作记录", OperationsPanel)}[key]
        window, created = single_window(self, f"_{key}_window", f"CPlus Cleaner · {title}")
        if not created:
            return window
        window.withdraw()
        window.configure(bg=WHITE)
        window.transient(self)
        minimum = (820, 520) if key == "operations" else (780, 620)
        window.minsize(*minimum)
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.bind("<Escape>", lambda event: window.destroy())
        panel = factory(window, self)
        panel.pack(fill="both", expand=True)
        window.panel = panel
        self.views[key] = panel
        if key in {"settings", "operations"}:
            panel.refresh()
        if key == "operations":
            window.reload_records = panel.refresh

        def release(event):
            if event.widget is window and self.views.get(key) is panel:
                self.views.pop(key, None)
        window.bind("<Destroy>", release, add="+")
        window.update_idletasks()
        width = 960 if key == "operations" else 780
        height = max(620, panel.winfo_reqheight())
        x = max(0, self.winfo_rootx()+(self.winfo_width()-width)//2)
        y = max(0, self.winfo_rooty()+(self.winfo_height()-height)//2)
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.deiconify()
        window.lift()
        window.focus_force()
        return window

    def apply_settings(self, values):
        if self.busy:
            raise ValueError("请等待当前任务结束后再保存设置")
        values = preferences.validate(values)
        if values["close_to_tray"] or values["autostart"]:
            self.ensure_tray()
        previous_autostart = preferences.read_autostart()
        changed = values["autostart"] != previous_autostart
        if changed:
            preferences.set_autostart(values["autostart"])
        try:
            values = preferences.save(values)
        except OSError:
            if changed:
                preferences.set_autostart(previous_autostart)
            raise
        self.settings = values
        self.scheduler.configure(values)
        i18n.set_language(values['language'])
        self.update_language_layout()
        refresh_tray = getattr(self.tray_icon, 'refresh_language', None)
        if refresh_tray:
            refresh_tray()

    def update_language_layout(self):
        english = i18n.language() == 'en'
        self.sidebar.configure(width=244 if english else 214)
        self.minsize(1120 if english else 980, 650)

    def ensure_tray(self):
        if self.tray_icon and self.tray_icon.hwnd:
            return
        candidate = tray.NativeTray(self.tray_events, ASSETS / "tray.ico")
        if not candidate.start():
            candidate.stop()
            raise OSError(candidate.error or "无法创建系统托盘图标")
        self.tray_icon = candidate

    def hide_to_tray(self):
        self.more_menu.close()
        try:
            self.ensure_tray()
        except OSError as exc:
            self.status.set(f"隐藏失败：{exc}")
            self.deiconify()
            return
        self.withdraw()

    def restore_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def close(self):
        if self.settings["close_to_tray"]:
            self.hide_to_tray()
        else:
            self.quit_app()

    def quit_app(self):
        if self.busy:
            messagebox.showinfo("任务仍在运行", "请先取消并等待任务结束，再退出软件。")
            return
        self.destroy()

    def destroy(self):
        self._closing = True
        if hasattr(self, "more_menu"):
            self.more_menu.close()
        if self._popup_resume is not None:
            self.after_cancel(self._popup_resume)
            self._popup_resume = None
        if self._schedule_timer is not None:
            self.after_cancel(self._schedule_timer)
            self._schedule_timer = None
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None
        super().destroy()

    def resume_popup_blocking(self):
        self._popup_resume = None
        if self._closing or "popup" in self.views:
            return
        from cplus_cleaner.features.popup.popup import Blocker
        if not Blocker().enabled:
            return
        if self.busy:
            self._popup_resume = self.after(250, self.resume_popup_blocking)
            return
        from cplus_cleaner.features.popup.popup_ui import PopupPage
        page = PopupPage(self.notebook, self)
        self.views["popup"] = page
        self.notebook.add(page, text="popup")
        page.scan()

    def schedule_tick(self):
        self._schedule_timer = None
        try:
            if self.scheduler.due(busy=self.busy):
                self.run_scheduled_cleanup()
        finally:
            if not self._closing:
                self._schedule_timer = self.after(15000, self.schedule_tick)

    def run_scheduled_cleanup(self):
        if self.busy or not self.settings["scheduled_cleanup"]:
            return
        from datetime import datetime
        values = dict(self.settings, last_cleanup_date=datetime.now().date().isoformat())
        try:
            # Persist before any deletion so a restart cannot repeat today's job.
            preferences.save(values)
            preferences.append_activity("scheduled_cleanup", summary="开始定时清理默认常规类型")
        except OSError as exc:
            self.status.set(f"定时清理未开始：{exc}")
            return
        self.settings = values
        self.scheduler.mark_started()
        def work():
            result = preferences.scheduled_cleanup(self.cancel, self.report_progress)
            preferences.append_activity("scheduled_cleanup", ok=not result["errors"] and not result["cancelled"],
                summary=f"删除 {result['deleted']} 项，释放 {size_text(result['bytes'])}" + ("；任务已取消" if result["cancelled"] else ""), result=result)
            return result
        def done(result):
            from cplus_cleaner.features.home.home_results import refresh_cleanup
            refresh_cleanup(self)
            self.home_page.refresh()
            self.status.set(f"定时清理{'已取消' if result['cancelled'] else '完成'}：释放 {size_text(result['bytes'])}，{len(result['errors'])} 项失败")
            if self._operations_window is not None:
                self.views["operations"].refresh()
        def failed(error):
            try:
                preferences.append_activity("scheduled_cleanup", ok=False, error=error)
            except OSError:
                pass
        self.start("定时清理默认常规类型", work, done, failed)

    def ensure_view(self, key):
        if key in {"settings", "feedback", "operations", "donation"}:
            return self.open_panel_window(key).panel
        if key not in self.views:
            def migration_view():
                from cplus_cleaner.features.app_mover.software_migration_ui import SoftwareMigrationPage
                return SoftwareMigrationPage(self.notebook, self)
            def large_view():
                from cplus_cleaner.features.large_files.large_files_ui import LargeFilesPage
                return LargeFilesPage(self.notebook, self)
            def privacy_view():
                from cplus_cleaner.features.privacy.privacy_ui import PrivacyPage
                return PrivacyPage(self.notebook, self)
            def uninstall_view():
                from cplus_cleaner.features.uninstall.software_uninstall_ui import SoftwareUninstallPage
                return SoftwareUninstallPage(self.notebook, self)
            def compression_view():
                from cplus_cleaner.features.app_compression.software_compression_ui import SoftwareCompressionPage
                return SoftwareCompressionPage(self.notebook, self)
            def popup_view():
                from cplus_cleaner.features.popup.popup_ui import PopupPage
                return PopupPage(self.notebook, self)
            def duplicates_view():
                from cplus_cleaner.features.duplicates.duplicates_ui import DuplicatesPage
                return DuplicatesPage(self.notebook, self)
            def chat_view():
                from cplus_cleaner.features.chat.chat_ui import ChatPage
                return ChatPage(self.notebook, self)
            def system_view():
                from cplus_cleaner.features.system.system_management_ui import SystemManagementPage
                return SystemManagementPage(self.notebook, self)
            factories = {"chat": chat_view,
                         "large": large_view,
                         "duplicates": duplicates_view,
                         "privacy": privacy_view,
                         "migrate": migration_view,
                         "compress": compression_view,
                         "uninstall": uninstall_view,
                         "popup": popup_view, "system": system_view}
            if key not in factories:
                return
            page = factories[key]()
            self.views[key] = page
            self.notebook.add(page, text=key)
        return self.views.get(key)

    def navigate(self, key, *, use_existing=False):
        if key in {"settings", "feedback", "operations"}:
            return self.open_panel_window(key)
        self.previous_tool = getattr(self, "current_page", "home")
        home = self.home_page
        if not use_existing and not self.busy and not home.scanning and key in home.results:
            if key in home.adopted:
                use_existing = True
            else:
                from cplus_cleaner.features.home.home_results import apply_result
                if apply_result(self, key, home.results[key]):
                    home.adopted.add(key)
                    use_existing = True
        if self.ensure_view(key) is None:
            return
        self.current_page = key
        self.notebook.select(self.views[key])
        if key in {"popup", "home", "system"}:
            self.footer.pack_forget()
        elif not self.footer.winfo_manager():
            self.footer.pack(side="bottom", fill="x", padx=22, pady=(5, 8), before=self.notebook)
        if key == "duplicates":
            self.status_label.pack_forget()
        elif not self.status_label.winfo_manager():
            self.status_label.pack(side="left", fill="x", expand=True)
        for name, (row, marker, label) in self.nav_widgets.items():
            active = name == key
            row.configure(bg=PALE if active else WHITE)
            marker.configure(bg=MINT if active else WHITE)
            label.configure(bg=PALE if active else WHITE, fg="#1abb99" if active else "#719397")
            icon_label, normal, highlighted = self.nav_icons[name]
            icon_label.configure(bg=PALE if active else WHITE, image=(highlighted if active else normal) or "")
        if key == "home":
            self.home_page.refresh()
        elif key in {"cdrive", "chat"}:
            self.views[key].refresh()
        elif key in {"migrate", "large", "uninstall", "compress", "popup", "system"} and not use_existing:
            self.views[key].on_open()


if __name__ == "__main__":
    import sys
    from cplus_cleaner.application.single_instance import run
    run(background='--background' in sys.argv)

# Purpose: Main window, navigation, feature composition and settings dialog
