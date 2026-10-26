"""Duplicate-file page for the current desktop, with retained-copy checks."""
from __future__ import annotations

import os
from pathlib import Path
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.localization.localized_tk import filedialog, messagebox, ttk

from cplus_cleaner.common import core
from cplus_cleaner.ui.desktop import WHITE, INK, MUTED, FONT, PillButton, size_text, text_button
from cplus_cleaner.ui.ui_tasks import operation_status
from cplus_cleaner.ui.ui_widgets import MetricLabel, check_images


class DuplicatesPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=WHITE)
        self.app, self.kind = app, "重复文件"
        self.records, self.checked, self.groups = {}, set(), []
        self.scanning = False
        self.folder = tk.StringVar(value=str(Path.home()/"Downloads"))
        self.total = tk.StringVar(value="尚未扫描，扫描结果默认不勾选。")
        self.scan_note = tk.StringVar()
        self.note = self.scan_note
        self.check_images = check_images(self)

        header = tk.Frame(self, bg=WHITE)
        header.pack(fill="x", padx=30, pady=(27, 18))
        tk.Label(header, text="重复文件清理", bg=WHITE, fg=INK, font=(FONT, 20)).pack(side="left")
        self.scan_button = PillButton(header, "开始扫描", self.scan, width=150)
        self.scan_button.pack(side="right")
        location = tk.Frame(self, bg=WHITE)
        location.pack(fill="x", padx=30, pady=(0, 12))
        tk.Label(location, text="扫描目录", bg=WHITE, fg=MUTED, font=(FONT, 10)).pack(side="left")
        ttk.Entry(location, textvariable=self.folder, style="Duplicates.TEntry").pack(side="left", fill="x", expand=True, padx=10)
        text_button(location, "选择目录", self.choose_folder).pack(side="right")
        actions = tk.Frame(self, bg=WHITE)
        actions.pack(fill="x", padx=25, pady=(0, 8))
        for caption, command in (("全选", lambda: self.select(True)), ("全不选", lambda: self.select(False)),
                                  ("每组保留第一份", self.smart_select)):
            text_button(actions, caption, command).pack(side="left")
        text_button(actions, "打开位置", self.open_location).pack(side="right")

        self.bottom = tk.Frame(self, bg=WHITE)
        self.bottom.pack(side="bottom", fill="x", padx=30, pady=(8, 15))
        tk.Label(self.bottom, text="永久删除，不经过回收站。", bg=WHITE, fg=MUTED,
                 font=(FONT, 9)).pack(side="left")
        self.clean_button = PillButton(self.bottom, "删除", self.clean, width=112, height=36)
        self.clean_button.pack(side="right")
        self.scan_note_label = tk.Label(self, textvariable=self.scan_note, bg=WHITE, fg=MUTED,
                                        font=(FONT, 9), wraplength=780, justify="left", anchor="w")
        self.scan_note_label.pack(side="bottom", fill="x", padx=30, pady=(2, 5))
        self.total_label = MetricLabel(self, textvariable=self.total, bg=WHITE, fg=MUTED, font=(FONT, 10))
        self.total_label.pack(side="bottom", fill="x", padx=30, pady=(8, 3))

        area = tk.Frame(self, bg=WHITE)
        area.pack(fill="both", expand=True, padx=30)
        style = ttk.Style(self)
        style.configure("Duplicates.TEntry", fieldbackground=WHITE, foreground=INK, padding=(8, 5),
                        bordercolor="#cce4e3", lightcolor=WHITE, darkcolor=WHITE)
        style.configure("Duplicates.Treeview", rowheight=31, background=WHITE, fieldbackground=WHITE,
                        foreground="#65858d", font=(FONT, 9), borderwidth=0)
        style.layout("Duplicates.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Duplicates.Treeview.Heading", background="#f2f6fa", foreground="#719397",
                        font=(FONT, 9), relief="flat", borderwidth=0)
        self.tree = ttk.Treeview(area, columns=("size", "category", "path"), show="tree headings",
                                 selectmode="extended", style="Duplicates.Treeview")
        self.tree.heading("#0", text="勾选")
        self.tree.column("#0", width=48, minwidth=42, stretch=False)
        for key, caption, width in (("size", "大小", 90), ("category", "重复组", 135), ("path", "文件路径", 400)):
            self.tree.heading(key, text=caption)
            self.tree.column(key, width=width, minwidth=55, stretch=key == "path")
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Button-1>", self.toggle)
        self.tree.bind("<space>", self.toggle_selection)
        self.update_total()

    def choose_folder(self):
        value = filedialog.askdirectory(parent=self, title="选择重复文件扫描目录")
        if value:
            self.folder.set(value)

    def scan(self):
        if self.app.busy:
            return
        folder = Path(self.folder.get())
        self.clear_results()
        self.scanning = True
        self.scan_button.set_enabled(False)
        self.scan_note.set("正在按文件大小和SHA-256核对内容，可取消扫描。")
        self.update_total()
        def done(groups):
            self.scanning = False
            self.populate(groups)
            self.scan_button.caption = "扫描"
            self.scan_button.set_enabled(True)
            self.scan_note.set("扫描已取消，保留已找到的重复组。" if self.app.cancel.is_set() else
                               "扫描完成，每组至少保留一份文件。" if self.records else "未发现重复文件。")
        def failed(error):
            self.scanning = False
            self.scan_button.set_enabled(True)
            self.scan_note.set(f"扫描未完成：{error}")
            self.update_total()
        self.app.start("扫描重复文件", lambda: core.duplicate_files(folder, self.app.cancel, self.app.report_progress), done, failed)

    def clear_results(self):
        self.tree.delete(*self.tree.get_children())
        self.records.clear()
        self.checked.clear()
        self.groups = []
        self.scan_note.set("")

    def append_items(self, items, category=None):
        for item in items:
            iid = str(len(self.records))
            self.records[iid] = item
            self.tree.insert("", "end", iid=iid, image=self.check_images["off"],
                             values=(size_text(item.size), category or item.category, item.path))
        self.update_total()

    def populate(self, groups):
        self.clear_results()
        self.groups = list(groups)
        for index, group in enumerate(self.groups):
            self.append_items(group, f"组 {index+1} · SHA-256")
        self.update_total()

    def toggle(self, event):
        if not self.app.busy and self.tree.identify_column(event.x) == "#0":
            iid = self.tree.identify_row(event.y)
            if iid:
                self.set_check(iid, iid not in self.checked)
                self.update_total()

    def toggle_selection(self, event=None):
        if not self.app.busy:
            for iid in self.tree.selection():
                self.set_check(iid, iid not in self.checked)
            self.update_total()
        return "break"

    def set_check(self, iid, checked):
        if checked:
            self.checked.add(iid)
        else:
            self.checked.discard(iid)
        self.tree.item(iid, image=self.check_images["on" if checked else "off"])

    def select(self, checked):
        if not self.app.busy:
            for iid in self.records:
                self.set_check(iid, checked)
            self.update_total()

    def smart_select(self):
        if self.app.busy:
            return
        seen = set()
        for iid, item in self.records.items():
            self.set_check(iid, item.group in seen)
            seen.add(item.group)
        self.update_total()

    def update_total(self):
        amount = sum(self.records[iid].size for iid in self.checked)
        self.total.set(f"{'扫描中 · ' if self.scanning else ''}发现 {len(self.records)} 个文件　|　"
                       f"勾选 {len(self.checked)} 个　|　逻辑大小 {size_text(amount)}")
        groups = {}
        for item in self.records.values():
            groups.setdefault(item.group, []).append(item.size)
        self.app.set_nav_metric("duplicates", sum(sum(sizes)-max(sizes) for sizes in groups.values()))
        self.clean_button.set_enabled(bool(self.checked) and not self.app.busy and not self.scanning)

    def clean(self):
        if self.app.busy or not self.checked:
            return
        ids = list(self.checked)
        keep = {item.group for iid, item in self.records.items() if iid not in self.checked}
        if any(self.records[iid].group not in keep for iid in ids):
            messagebox.showerror("保留副本", "每组重复文件至少保留一份。请取消勾选一份，或使用智能选择。", parent=self)
            return
        retained = {}
        for iid, item in self.records.items():
            if iid not in self.checked:
                retained.setdefault(item.group, item)
        items = [self.records[iid] for iid in ids]
        def work():
            for group, item in retained.items():
                path = core.validate_candidate(item)
                if core.sha256_file(path, self.app.cancel) != group:
                    raise ValueError("保留副本已改变，请重新扫描重复文件")
            return core.delete_files(items, self.app.cancel, self.app.report_progress)
        def done(result):
            for iid in ids:
                if not Path(self.records[iid].path).exists():
                    self.tree.delete(iid)
                    self.records.pop(iid)
                    self.checked.discard(iid)
            self.update_total()
            operation_status(self, f"已删除 {result['deleted']} 个文件，释放 {size_text(result['bytes'])}。"
                             f"跳过/失败 {len(result['errors'])} 项。" + ("任务已取消。" if result['cancelled'] else ""))
        def failed(error):
            self.update_total()
            operation_status(self, f"删除未完成：{error}")
        self.clean_button.set_enabled(False)
        self.app.start("删除重复文件", work, done, failed)

    def open_location(self):
        selected = self.tree.selection()
        if selected and os.name == "nt":
            os.startfile(str(Path(self.records[selected[0]].path).parent))

# Purpose: Duplicate scan, retention and deletion UI
