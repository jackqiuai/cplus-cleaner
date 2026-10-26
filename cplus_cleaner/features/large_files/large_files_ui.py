"""Large-file landing page and expandable category results."""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import subprocess
import time
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.localization.i18n import original
from cplus_cleaner.localization.localized_tk import filedialog, messagebox, ttk

from cplus_cleaner.features.large_files import large_files as files
from cplus_cleaner.ui.ui_widgets import check_images, single_window, MetricLabel
from cplus_cleaner.ui.desktop import WHITE, INK, MUTED, MINT, PALE, LINE, CORAL, FONT, DiskBar, PillButton, size_text, text_button, disk_name


class LargeFilesPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=WHITE)
        self.app = app
        self.check_images = check_images(self)
        self.items, self.selected, self.groups, self.row_items = [], set(), {}, {}
        self.blocked_paths = {}
        self.scanned = False
        self.scanning = False
        self._progress_stamp = 0
        self.destination = tk.StringVar(value=files.default_destination())
        self.note = tk.StringVar(value="按原版目录规则扫描C盘全部时间的大文件，包含AppData；受保护文件会标明原因。")
        self.heading = tk.StringVar(value="一键转移大文件，为C盘瘦身")
        self.selection = tk.StringVar()
        self.disk_text = tk.StringVar()
        top = tk.Frame(self, bg=WHITE)
        top.pack(fill="x", padx=32, pady=(28, 0))
        self.scan_button = PillButton(top, "开始扫描", self.scan, width=142)
        self.scan_button.pack(side="right")
        self.move_button = PillButton(top, "立即搬家", self.move_selected, width=142)
        self.title_frame = tk.Frame(top, bg=WHITE)
        self.title_frame.pack(side="left", fill="x", expand=True, pady=5)
        self.title = tk.Label(self.title_frame, textvariable=self.heading, bg=WHITE, fg=INK, font=(FONT, 21), anchor="w")
        self.title.pack(side="left")
        self.total_text = tk.StringVar()
        tk.Label(self.title_frame, textvariable=self.total_text, bg=WHITE, fg=CORAL,
                 font=(FONT, 18, "bold")).pack(side="left", padx=(8, 0))
        tk.Label(self, textvariable=self.disk_text, bg=WHITE, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=34, pady=(6, 6))
        self.progress_frame = tk.Frame(self, bg=WHITE)
        self.progress_text = tk.StringVar()
        tk.Label(self.progress_frame, textvariable=self.progress_text, bg=WHITE, fg=MUTED, anchor="w",
                 font=(FONT, 9)).pack(fill="x", pady=(0, 4))
        self.progress_value = tk.DoubleVar(value=0)
        style = ttk.Style(self)
        # Copy just these flat elements; keep the other pages' theme unchanged.
        style.element_create("Large.Progressbar.trough", "from", "clam", "Horizontal.Progressbar.trough")
        style.element_create("Large.Progressbar.pbar", "from", "clam", "Horizontal.Progressbar.pbar")
        style.layout("Large.Horizontal.TProgressbar", [("Large.Progressbar.trough", {
            "sticky": "nswe", "children": [("Large.Progressbar.pbar", {"side": "left", "sticky": "ns"})]})])
        style.configure("Large.Horizontal.TProgressbar", background=MINT, troughcolor=PALE,
                        borderwidth=0, thickness=8)
        self.page_progress = ttk.Progressbar(self.progress_frame, maximum=100, variable=self.progress_value,
                                             style="Large.Horizontal.TProgressbar")
        self.page_progress.pack(fill="x")

        self.footer = tk.Frame(self, bg="#f3f8fa")
        self.footer.pack(side="bottom", fill="x")
        text_button(self.footer, "搬家记录", self.show_history).pack(side="right", padx=(0, 14), pady=8)
        text_button(self.footer, "选择其他位置", self.choose_destination).pack(side="right", pady=8)
        tk.Label(self.footer, text="搬家到：", bg="#f3f8fa", fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(15, 0))
        tk.Label(self.footer, textvariable=self.destination, bg="#f3f8fa", fg=CORAL,
                 font=(FONT, 9), width=1, anchor="w").pack(side="left", fill="x", expand=True)
        tk.Label(self, textvariable=self.note, bg=WHITE, fg=MUTED, font=(FONT, 9), anchor="w",
                 wraplength=820, justify="left").pack(side="bottom", fill="x", padx=32, pady=10)

        self.idle = tk.Frame(self, bg=WHITE)
        self.idle.pack(fill="both", expand=True, padx=32, pady=(35, 8))
        tk.Label(self.idle, text="深度扫描，智能识别各类大文件", bg=WHITE, fg="#719397", font=(FONT, 11)).pack(anchor="w")
        labels = tk.Frame(self.idle, bg=WHITE)
        labels.pack(anchor="w", pady=(18, 20))
        for i, (name, symbol) in enumerate((("电脑安装包", "c-azb"), ("系统压缩包", "c-ysb"),
                                          ("办公文档", "c-bgwd"), ("日志文件", "c-file"), ("视频文件", "c-play"))):
            icon = app.images.get(f"large-{symbol}.png")
            tk.Label(labels, text="  " + name, image=icon or "", compound="left", bg=WHITE, fg=MUTED,
                     font=(FONT, 10)).grid(row=i//3, column=i%3, sticky="w", padx=(0, 20), pady=7)
        body = tk.Frame(self.idle, bg=WHITE)
        body.pack(fill="both", expand=True)
        self.disks = tk.Frame(body, bg=WHITE)
        self.disks.pack(side="left", fill="y", pady=(25, 0))
        self.art = tk.Label(self.idle, bg=WHITE, bd=0, padx=0, pady=0)
        self.art.place(relx=1, rely=1, anchor="se", x=-2, y=-8)
        image = app.images.get("hero-large.png")
        if image:
            self.art.configure(image=image)
        def fit_art(event):
            if event.width < 40 or event.height < 40:
                return
            artwork = app.images.illustration("hero-large.png", min(300, event.width*.42),
                                              min(330, event.height-16))
            self.art.configure(image=artwork or "")
        self.idle.bind("<Configure>", fit_art)
        self.disk_bars = []

        self.results = tk.Frame(self, bg=WHITE)
        controls = tk.Frame(self.results, bg=WHITE)
        controls.pack(fill="x", padx=30, pady=(6, 6))
        self.rescan_button = text_button(controls, "↻ 扫描", self.scan)
        self.rescan_button.pack(side="right")
        self.delete_button = text_button(controls, "删除所选", self.delete_selected, CORAL)
        self.delete_button.pack(side="right", padx=12)
        MetricLabel(controls, textvariable=self.selection, bg=WHITE, fg="#719397", font=(FONT, 10)).pack(side="left", pady=5)
        self.table_header = tk.Frame(self.results, bg="#f2f7f9")
        self.table_header.pack(fill="x", padx=25, pady=(5, 0))
        headings = (("名称", 0), ("大小", 100), ("数量", 60), ("删除", 60), ("搬家", 60), ("位置", 60))
        for column, (caption, width) in enumerate(headings):
            self.table_header.columnconfigure(column, weight=1 if column == 0 else 0, minsize=width)
            if column == 0:
                self.select_all_button = text_button(self.table_header, "名称", self.toggle_all)
                self.select_all_button.configure(bg="#f2f7f9", anchor="w", image=self.check_images["off"], compound="left")
                self.select_all_button.grid(row=0, column=column, sticky="ew", padx=(8, 0), pady=2)
            else:
                tk.Label(self.table_header, text=caption, bg="#f2f7f9", fg="#719397", font=(FONT, 10)).grid(
                    row=0, column=column, sticky="ew", pady=7)
        self.table_header.columnconfigure(6, minsize=18)
        table = tk.Frame(self.results, bg=WHITE)
        table.pack(fill="both", expand=True, padx=25)
        style.configure("Large.Treeview", font=(FONT, 10), rowheight=34, background=WHITE,
                        fieldbackground=WHITE, foreground="#65858d", borderwidth=0)
        style.layout("Large.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.map("Large.Treeview", background=[("selected", PALE)], foreground=[("selected", INK)])
        style.configure("Large.Treeview.Heading", font=(FONT, 10), foreground="#65858d")
        self.tree = ttk.Treeview(table, columns=("size", "count", "delete", "move", "location"),
                                 show="tree", selectmode="browse", style="Large.Treeview")
        self.tree.heading("#0", text="名称")
        self.tree.column("#0", width=340, minwidth=160, stretch=True)
        for name, title, width in (("size", "大小", 100), ("count", "数量", 60), ("delete", "删除", 60),
                                    ("move", "搬家", 60), ("location", "位置", 60)):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, minwidth=width, stretch=False, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.tag_configure("group", background="#f3f8fa", foreground=INK)
        self.tree.tag_configure("blocked", foreground=MUTED)
        self.tree.bind("<Button-1>", self.click)
        self.tree.bind("<space>", self.keyboard_toggle)
        self.tree.bind("<<TreeviewOpen>>", self.expand_focused)
        self.tree.bind("<<TreeviewSelect>>", self.show_path)
        self.tree.bind("<Button-3>", self.context_menu)
        self.move_button.set_enabled(False)
        self.delete_button.configure(state="disabled")

    def on_open(self):
        self.refresh_disks()

    def refresh_disks(self):
        info = files.disk_inventory()
        system = next((entry for entry in info if Path(entry["root"]).drive.casefold() == files.system_drive().drive.casefold()), None)
        for bar in self.disk_bars:
            bar.destroy()
        self.disk_bars = []
        overview = [(None, system, CORAL)] if system else []
        if info:
            total = dict(root="总计", **{key: sum(item[key] for item in info)
                                       for key in ("used", "total", "free")})
            overview.append(("磁盘总容量", total, "#ff9847"))
        for title, entry, color in overview:
            bar = DiskBar(self.disks, title, wide=330, color=color)
            bar.pack(fill="x", pady=(0, 18))
            bar.update_info(entry)
            self.disk_bars.append(bar)
        self.disk_text.set(f"{disk_name(system)}共 {size_text(system['total'])}，可用 {size_text(system['free'])}" if system else "")

    def start_progress(self, message):
        self.progress_frame.pack(fill="x", padx=34, pady=8, before=self.results if self.scanned else self.idle)
        self.progress_value.set(0)
        self.page_progress.configure(mode="indeterminate")
        self.page_progress.start(20)
        self.progress_text.set(message)
        self._progress_stamp = 0

    def report_progress(self, data):
        if time.monotonic() - self._progress_stamp >= .08 or data.get("stage") != "copy":
            self.app.events.put(("batch", (self.consume_progress, data)))
            self._progress_stamp = time.monotonic()

    def consume_progress(self, data):
        done, total = data.get("completed"), data.get("total")
        known = done is not None and total is not None and total > 0
        mode = "determinate" if known else "indeterminate"
        if str(self.page_progress.cget("mode")) != mode:
            self.page_progress.stop()
            self.page_progress.configure(mode=mode)
            if not known:
                self.page_progress.start(20)
        suffix = ""
        if known:
            amount = min(100, done/total*100)
            self.progress_value.set(amount)
            suffix = f"（{amount:.0f}%）"
        self.progress_text.set(data["message"] + suffix)

    def finish_progress(self, message, complete=True):
        known = str(self.page_progress.cget("mode")) == "determinate"
        value = self.progress_value.get() if known else 0
        self.page_progress.stop()
        self.page_progress.configure(mode="determinate")
        self.progress_value.set(100 if complete else min(99, value))
        self.progress_text.set(message)
        if complete:
            self.progress_frame.pack_forget()

    def scan(self):
        if self.app.busy:
            return
        self.scan_button.set_enabled(False)
        self.scanning = True
        self.start_progress("正在扫描C盘全部时间的大文件…")
        self.heading.set("正在查找可搬家的大文件…")
        self.total_text.set("")
        def done(result):
            self.scanning = False
            self.scan_details = result
            self.items = result["items"]
            self.blocked_paths = result.get("blocked_paths", {})
            self.selected = {item.path for item in self.items if item.path not in self.blocked_paths}
            self.scanned = True
            self.idle.pack_forget()
            self.results.pack(fill="both", expand=True)
            self.render()
            self.scan_button.caption = "扫描"
            self.scan_button.set_enabled(True)
            self.scan_button.pack_forget()
            self.move_button.pack(side="right", before=self.title_frame)
            self.finish_progress("扫描已取消，保留已发现结果" if result["cancelled"] else "扫描完成", not result["cancelled"])
            self.note.set(f"全部时间 · {files.threshold_description(result['minimum'])}；按原版目录规则扫描。" +
                          (f" {len(self.blocked_paths)}项受保护或运行中，仅展示。" if self.blocked_paths else "") +
                          (f" {result['counts'].get('errors', 0)} 项无法读取或已跳过。" if result['counts'].get('errors') else ""))
            self.refresh_disks()
        def failed(error):
            self.scanning = False
            self.scan_button.set_enabled(True)
            self.finish_progress("扫描未完成", False)
            self.note.set(str(error))
            self.update_summary()
        self.app.start("扫描C盘大文件", lambda: files.scan(self.app.cancel, self.report_progress), done, failed)
        self.update_summary()

    def render(self):
        opened = {self.tree.item(key, "text") for key in self.tree.get_children() if self.tree.item(key, "open")}
        self.tree.delete(*self.tree.get_children())
        self.groups, self.row_items = {}, {}
        for index, name in enumerate(files.TYPES):
            group = [item for item in self.items if item.category == name]
            if not group:
                continue
            key = f"g{index}"
            self.groups[key] = group
            self.tree.insert("", "end", iid=key, text=name, image=self.check_images["off"], tags=("group",),
                values=(size_text(sum(item.size for item in group)), f"{len(group)}项", "删除", "搬家", ""))
            self.tree.insert(key, "end", iid=key + "-pending", text="正在展开…")
            if name in opened:
                self.populate(key)
                self.tree.item(key, open=True)
        self.update_summary()

    def populate(self, key):
        placeholder = key + "-pending"
        if not self.tree.exists(placeholder):
            return
        self.tree.delete(placeholder)
        for index, item in enumerate(self.groups[key]):
            row = f"{key}-f{index}"
            self.row_items[row] = item
            blocked = item.path in self.blocked_paths
            self.tree.insert(key, "end", iid=row, text=original(Path(item.path).name), image=self.check_images["off"],
                tags=("blocked",) if blocked else (),
                values=(size_text(item.size), "", "—" if blocked else "删除", "受保护" if blocked else "搬家", "打开"))
        self.update_checks()

    def expand_focused(self, event=None):
        key = self.tree.focus()
        if key in self.groups:
            self.populate(key)

    def requested(self, key):
        return self.groups[key] if key in self.groups else ([self.row_items[key]] if key in self.row_items else [])

    def toggle(self, key):
        if self.app.busy:
            return
        paths = {item.path for item in self.requested(key) if item.path not in self.blocked_paths}
        if not paths:
            rows = self.requested(key)
            if rows:
                self.note.set(self.blocked_paths.get(rows[0].path, "此项目不能操作"))
            return
        if paths <= self.selected:
            self.selected.difference_update(paths)
        else:
            self.selected.update(paths)
        self.update_summary()

    def toggle_all(self):
        if not self.app.busy:
            paths = {item.path for item in self.items if item.path not in self.blocked_paths}
            self.selected = set() if paths <= self.selected else paths
            self.update_summary()

    def update_checks(self):
        all_paths = {item.path for item in self.items if item.path not in self.blocked_paths}
        state = "on" if all_paths and all_paths <= self.selected else "mixed" if all_paths & self.selected else "off"
        self.select_all_button.configure(image=self.check_images[state])
        for key, group in self.groups.items():
            paths = {item.path for item in group if item.path not in self.blocked_paths}
            state = "on" if paths and paths <= self.selected else "mixed" if paths & self.selected else "off"
            self.tree.item(key, image=self.check_images[state])
        for key, item in self.row_items.items():
            self.tree.item(key, image=self.check_images["on" if item.path in self.selected else "off"])

    def update_summary(self):
        selected = [item for item in self.items if item.path in self.selected and item.path not in self.blocked_paths]
        if self.scanned and not self.scanning:
            self.heading.set("发现大文件共")
            self.total_text.set(size_text(sum(item.size for item in self.items)))
            self.title.configure(font=(FONT, 18))
            self.app.set_nav_metric("large", sum(item.size for item in self.items))
        self.selection.set(f"已选择 {len(selected)} 项，可释放空间：{size_text(sum(item.size for item in selected))}")
        self.update_checks()
        enabled = bool(selected) and not self.app.busy
        self.move_button.set_enabled(enabled)
        self.delete_button.configure(state="normal" if enabled else "disabled")

    def click(self, event):
        key = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not key or self.app.busy:
            return
        self.tree.focus(key)
        if column == "#0":
            element = self.tree.identify_element(event.x, event.y)
            if "indicator" in element:
                return
            if "image" in element:
                self.toggle(key)
            elif key in self.groups:
                self.populate(key)
                self.tree.item(key, open=not self.tree.item(key, "open"))
            else:
                self.tree.selection_set(key)
            return "break"
        if column == "#3":
            self.operate(self.requested(key), "delete")
        elif column == "#4":
            self.operate(self.requested(key), "move")
        elif column == "#5" and key in self.row_items:
            self.open_location(self.row_items[key].path)

    def keyboard_toggle(self, event=None):
        self.toggle(self.tree.focus())
        return "break"

    def show_path(self, event=None):
        rows = self.tree.selection()
        if rows and rows[0] in self.row_items:
            path = self.row_items[rows[0]].path
            self.note.set(path + (" · " + self.blocked_paths[path] if path in self.blocked_paths else ""))

    def context_menu(self, event):
        key = self.tree.identify_row(event.y)
        if self.app.busy or not self.requested(key):
            return
        self.tree.selection_set(key)
        if getattr(self, "popup_menu", None):
            self.popup_menu.destroy()
        menu = self.popup_menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="搬家此类别全部文件" if key in self.groups else "搬家此文件", command=lambda: self.operate(self.requested(key), "move"))
        menu.add_command(label="删除此类别全部文件" if key in self.groups else "删除此文件", command=lambda: self.operate(self.requested(key), "delete"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def choose_destination(self):
        if self.app.busy:
            return
        value = filedialog.askdirectory(parent=self, title="选择其他磁盘上的大文件搬家目录", initialdir=self.destination.get() or None)
        if value:
            try:
                files.save_destination(value)
                self.destination.set(value)
            except OSError as exc:
                self.note.set(f"目标位置保存失败：{exc}")

    def move_selected(self):
        self.operate([item for item in self.items if item.path in self.selected], "move")

    def delete_selected(self):
        self.operate([item for item in self.items if item.path in self.selected], "delete")

    def operate(self, items, action):
        if self.app.busy or not items:
            return
        allowed = [item for item in items if item.path not in self.blocked_paths]
        if not allowed:
            self.note.set(self.blocked_paths.get(items[0].path, "这些文件当前不能操作"))
            return
        items = allowed
        if action == "delete" and not messagebox.askyesno("永久删除文件", f"永久删除 {len(items)} 个文件，共 {size_text(sum(item.size for item in items))}？\n这些文件不会进入回收站。", parent=self):
            return
        if action == "move" and not self.destination.get():
            self.choose_destination()
            if not self.destination.get():
                return
        self.start_progress("正在搬家…" if action == "move" else "正在删除所选文件…")
        self.scan_button.set_enabled(False)
        destination = self.destination.get()
        def done(result):
            successful = set(result["success"])
            self.items = [item for item in self.items if item.path not in successful]
            self.selected.difference_update(successful)
            self.render()
            self.scan_button.set_enabled(True)
            operation = "搬家" if action == "move" else "删除"
            self.finish_progress(operation + ("已取消" if result["cancelled"] else "完成"), not result["cancelled"] and not result["errors"])
            self.note.set(f"{operation}成功 {len(successful)} 项，释放 {size_text(result['bytes'])}。" +
                          (f" {len(result['errors'])} 项未完成：{result['errors'][0]['error']}" if result["errors"] else ""))
            self.refresh_disks()
            self.app.home_page.refresh()
        def failed(error):
            self.scan_button.set_enabled(True)
            self.finish_progress("操作未完成", False)
            self.note.set(str(error))
            self.update_summary()
        self.app.start("大文件搬家" if action == "move" else "删除大文件",
            lambda: files.operate(items, action, destination, self.app.cancel, self.report_progress), done, failed)
        self.update_summary()

    def open_location(self, path):
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"
        subprocess.Popen([str(executable), "/select,", str(path)])

    def show_history(self):
        window, created = single_window(self, "_history_window", "大文件搬家记录")
        if not created:
            return window
        window.geometry("920x480")
        tree = ttk.Treeview(window, columns=("time", "size", "status"), show="tree headings")
        tree.heading("#0", text="文件名")
        for key, title in (("time", "时间"), ("size", "大小"), ("status", "结果")):
            tree.heading(key, text=title)
        tree.pack(fill="both", expand=True, padx=15, pady=15)
        detail = tk.StringVar(value="选择记录查看原位置和目标位置")
        tk.Label(window, textvariable=detail, anchor="w", justify="left", wraplength=860).pack(fill="x", padx=15, pady=10)
        records = []
        def reload():
            try:
                records[:] = files.records()
            except OSError as exc:
                detail.set(str(exc))
                return
            tree.delete(*tree.get_children())
            for index, record in enumerate(records):
                tree.insert("", "end", iid=str(index), text=original(Path(record["path"]).name),
                    values=(datetime.fromtimestamp(record["time"]).strftime("%Y-%m-%d %H:%M"), size_text(record["size"]), record["status"]))
        def selected(event=None):
            if tree.selection():
                record = records[int(tree.selection()[0])]
                detail.set(f"原位置：{record['path']}\n目标：{record['target']}")
        tree.bind("<<TreeviewSelect>>", selected)
        window.reload_records = reload
        reload()
        return window
