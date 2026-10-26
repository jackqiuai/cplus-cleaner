"""The software list, batch relocation and history shown by the desktop sidebar."""
from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
import time
import localized_tk as tk
from i18n import display_name
from localized_tk import filedialog, ttk

import core
import software_migration as migration
from ui_widgets import Checkbutton, single_window, MetricLabel, scroll_canvas
from desktop import WHITE, INK, MUTED, MINT, PALE, LINE, CORAL, FONT, PillButton, text_button, size_text, disk_snapshot, disk_name


class SoftwareRow(tk.Frame):
    def __init__(self, parent, page, item):
        super().__init__(parent, bg=WHITE)
        self.page, self.item = page, item
        self.configure_columns(self)
        self.value = tk.BooleanVar(value=item.key in page.selected)
        self.checkbox = Checkbutton(self, variable=self.value, command=self.toggle, bg=WHITE,
            selectcolor=PALE, activebackground=WHITE, highlightthickness=0, bd=0,
            state="disabled" if item.blocked else "normal")
        self.checkbox.grid(row=0, column=0, rowspan=2, sticky="w")
        image = page.icons.get(item.key)
        if image is None and item.icon:
            try:
                image = tk.PhotoImage(master=self, data=base64.b64encode(item.icon))
                page.icons[item.key] = image
            except tk.TclError:
                pass
        image = image or page.app.images.navigation("migrate")
        tk.Label(self, image=image or "", bg=WHITE).grid(row=0, column=1, rowspan=2, padx=(4, 9))
        self.name = tk.Label(self, text=display_name(item.name), width=1, anchor="w", bg=WHITE, fg=INK, font=(FONT, 10))
        self.name.grid(row=0, column=2, sticky="ew", pady=(13, 0))
        self.path = tk.Label(self, width=1, anchor="w", bg=WHITE, fg=MUTED, font=(FONT, 9))
        self.path.grid(row=1, column=2, sticky="ew", pady=(0, 13))
        tk.Label(self, text=self.age(item.modified), bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(row=0, column=3, rowspan=2)
        tk.Label(self, text=size_text(item.size), bg=WHITE, fg=MUTED, font=(FONT, 9)).grid(row=0, column=4, rowspan=2)
        self.action = PillButton(self, "立即搬家" if not item.blocked else "暂不可搬", lambda: page.move_items([item]), width=104, height=32)
        self.action.grid(row=0, column=5, rowspan=2, padx=(8, 0))
        self.action.set_enabled(not item.blocked and not page.app.busy)
        self.bind("<Configure>", self.fit)
        self.name.bind("<Configure>", self.fit)
        tk.Frame(self, bg=LINE, height=1).grid(row=2, column=0, columnspan=6, sticky="ew")

    def destroy(self):
        super().destroy()
        # Destroyed widgets retain parent/child Python references. Release the
        # Tcl variable here on the UI thread instead of deferring its destructor
        # to a cyclic-GC pass that may run in the next scanning worker.
        self.value = None

    @staticmethod
    def configure_columns(widget):
        for column, width in ((0, 28), (1, 44), (3, 96), (4, 85), (5, 112)):
            widget.columnconfigure(column, minsize=width)
        widget.columnconfigure(2, weight=1, minsize=130)

    @staticmethod
    def age(stamp):
        days = max(0, int((time.time() - stamp) / 86400))
        if days < 7:
            return "最近一周"
        if days < 30:
            return "一月之内"
        if days < 90:
            return "三月之内"
        return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")

    def fit(self, event=None):
        # Labels must never make the action column disappear in a narrow window.
        from tkinter import font
        width = max(90, self.grid_bbox(column=2, row=0)[2] - 6)
        path_text = (self.item.blocked + " · " if self.item.blocked else "") + self.item.source
        for label, value in ((self.name, display_name(self.item.name)), (self.path, path_text)):
            label.fit_text(value, width)

    def toggle(self):
        if self.page.app.busy:
            self.value.set(self.item.key in self.page.selected)
            return
        if self.value.get():
            self.page.selected.add(self.item.key)
        else:
            self.page.selected.discard(self.item.key)
        self.page.update_summary()


class SoftwareMigrationPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=WHITE)
        self.app = app
        self.items, self.rows, self.icons, self.selected = [], {}, {}, set()
        self.scanning, self._pending_open = False, None
        self.scan_summary = {}
        self._progress_stamp, self._progress_stage = 0, None
        self.sort_key, self.descending = "size", True
        self.destination = tk.StringVar(value=migration.default_destination())
        self.heading = tk.StringVar(value="软件搬家")
        self.disk = tk.StringVar()
        self.selection = tk.StringVar(value="已选择 0 项，可释放空间：0KB")
        self.note = tk.StringVar(value="点击“扫描”查找可搬家的软件。")
        header = tk.Frame(self, bg=WHITE)
        header.pack(fill="x", padx=34, pady=(27, 0))
        header.columnconfigure(1, weight=1)
        self.warning_image = app.images.get("status-warning.png")
        self.warning = tk.Label(header, image=self.warning_image or "", bg=WHITE)
        self.warning.grid(row=0, column=0, sticky="nw", padx=(0, 12), pady=2)
        self.heading_label = MetricLabel(header, textvariable=self.heading, bg=WHITE, fg=INK, font=(FONT, 17))
        self.heading_label.grid(row=0, column=1, sticky="nw", pady=(3, 0))
        header_actions = tk.Frame(header, bg=WHITE)
        header_actions.grid(row=0, column=2, sticky="ne", padx=(14, 0))
        self.move_button = PillButton(header_actions, "立即搬家", self.move_selected, width=145)
        self.move_button.pack(side="right")
        self.move_button.set_enabled(False)
        self.scan_button = PillButton(header_actions, "扫描", self.scan, width=145)
        self.scan_button.pack(side="right", padx=8)

        def fit_header(event):
            width = max(140, event.width-header_actions.winfo_reqwidth()-self.warning.winfo_reqwidth()-26)
            self.heading_label.configure(wraplength=width, font=(FONT, 12 if width < 380 else 14 if width < 440 else 17))
        header.bind("<Configure>", fit_header)
        tk.Label(self, textvariable=self.disk, bg=WHITE, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=35, pady=(8, 12))
        MetricLabel(self, textvariable=self.selection, bg=WHITE, fg="#229f7c", font=(FONT, 10)).pack(anchor="w", padx=35, pady=(0, 15))
        self.progress_frame = tk.Frame(self, bg=WHITE)
        self.progress_frame.pack(fill="x", padx=35, pady=(0, 12))
        self.progress_text = tk.StringVar(value="等待扫描")
        tk.Label(self.progress_frame, textvariable=self.progress_text, bg=WHITE, fg=MUTED,
                 anchor="w", font=(FONT, 9), wraplength=720).pack(fill="x", pady=(0, 5))
        ttk.Style(self).configure("Software.Horizontal.TProgressbar", background=MINT,
                                  troughcolor="#edf5f2", borderwidth=0, thickness=10)
        self.progress_value = tk.DoubleVar(value=0)
        self.page_progress = ttk.Progressbar(self.progress_frame, maximum=100, variable=self.progress_value,
            mode="determinate", style="Software.Horizontal.TProgressbar")
        self.page_progress.pack(fill="x")
        # Bottom actions are reserved before the scrolling list expands.
        bottom = tk.Frame(self, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        bottom.pack(side="bottom", fill="x", padx=20, pady=(4, 0))
        actions = tk.Frame(bottom, bg=WHITE)
        actions.pack(side="right")
        self.choose_button = text_button(actions, "选择其他位置", self.choose_destination)
        self.choose_button.pack(side="left")
        self.history_button = text_button(actions, "搬家记录", self.show_history)
        self.history_button.pack(side="left")
        tk.Label(bottom, text="搬家到：", bg=WHITE, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=(7, 0))
        self.destination_label = tk.Label(bottom, textvariable=self.destination, bg=WHITE, fg=CORAL,
                                         anchor="w", width=1, font=(FONT, 9))
        self.destination_label.pack(side="left", fill="x", expand=True)
        tk.Label(self, textvariable=self.note, bg=WHITE, fg=MUTED, anchor="w", justify="left", wraplength=740,
                 font=(FONT, 9)).pack(side="bottom", fill="x", padx=35, pady=(7, 3))
        table_header = tk.Frame(self, bg="#f2f7fa")
        table_header.pack(fill="x", padx=(35, 51))
        SoftwareRow.configure_columns(table_header)
        self.select_all_value = tk.BooleanVar()
        self.select_all_button = Checkbutton(table_header, variable=self.select_all_value, command=self.select_all,
            bg="#f2f7fa", activebackground="#f2f7fa", highlightthickness=0, bd=0)
        self.select_all_button.grid(row=0, column=0, pady=6)
        tk.Label(table_header, text="软件名称", bg="#f2f7fa", fg=MUTED, font=(FONT, 9), anchor="w").grid(row=0, column=1, columnspan=2, sticky="ew")
        for column, title, key in ((3, "修改时间 ↕", "modified"), (4, "大小 ↕", "size")):
            tk.Button(table_header, text=title, command=lambda value=key: self.sort(value), bg="#f2f7fa", fg=MUTED,
                      relief="flat", font=(FONT, 9), bd=0).grid(row=0, column=column)
        tk.Label(table_header, text="操作", bg="#f2f7fa", fg=MUTED, font=(FONT, 9)).grid(row=0, column=5)
        area = tk.Frame(self, bg=WHITE)
        area.pack(fill="both", expand=True, padx=35)
        self.canvas = tk.Canvas(area, bg=WHITE, highlightthickness=0)
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=WHITE)
        self.inner_window = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.inner_window, width=event.width))
        self.inner.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.empty = tk.Label(self.inner, text="尚未扫描，请点击“扫描”。", bg=WHITE, fg=MUTED, font=(FONT, 11), pady=50)
        self.empty.pack(fill="x")
        self.bind_all("<MouseWheel>", self.mousewheel, add="+")

    def mousewheel(self, event):
        target = self.winfo_containing(event.x_root, event.y_root)
        while target is not None:
            if target == self.canvas:
                return scroll_canvas(self.canvas, self.inner, event.delta)
            target = getattr(target, "master", None)

    def start_page_progress(self, message):
        self._progress_stamp, self._progress_stage = 0, None
        self.page_progress.stop()
        self.page_progress.configure(mode="indeterminate")
        self.progress_value.set(0)
        self.progress_text.set(message)
        self.page_progress.start(20)

    def report_stage_progress(self, data):
        # Called by the worker. Tk widgets are only touched by consume_progress.
        now = time.monotonic()
        stage = (data["stage"], data.get("item_index"))
        final = data.get("total") is not None and data.get("completed") == data.get("total")
        if stage != self._progress_stage or now - self._progress_stamp >= .08 or final:
            self._progress_stage, self._progress_stamp = stage, now
            self.app.events.put(("batch", (self.consume_progress, dict(data))))
            self.app.report_progress(data["message"])

    def consume_progress(self, data):
        done, total = data.get("completed"), data.get("total")
        known = done is not None and total is not None and total > 0
        mode = "determinate" if known else "indeterminate"
        if str(self.page_progress.cget("mode")) != mode:
            self.page_progress.stop()
            self.page_progress.configure(mode=mode)
            if not known:
                self.page_progress.start(20)
        prefix = f"第 {data['item_index']}/{data['item_total']} 款 · " if "item_index" in data else ""
        suffix = ""
        if known:
            fraction = min(100, max(0, done / total * 100))
            self.progress_value.set(fraction)
            suffix = (f" · {size_text(done)} / {size_text(total)}" if data["stage"] == "copy" else
                      f" · {done}/{total}") + f"（{fraction:.0f}%）"
        self.progress_text.set(prefix + data["message"] + suffix)

    def finish_page_progress(self, message, completed=True):
        known = str(self.page_progress.cget("mode")) == "determinate"
        value = self.progress_value.get() if known else 0
        self.page_progress.stop()
        self.page_progress.configure(mode="determinate")
        self.progress_value.set(100 if completed else min(99, value))
        self.progress_text.set(message)

    def on_open(self):
        if self._pending_open is not None:
            self.after_cancel(self._pending_open)
            self._pending_open = None
        self.update_summary()
        if not self.scanning and not self.scan_summary and not self.items:
            self.heading.set("软件搬家")
            self.empty.configure(text="尚未扫描，请点击“扫描”。")
        try:
            info = disk_snapshot()
            self.disk.set(f"{disk_name(info)}共 {size_text(info['total'])}，可用 {size_text(info['free'])}")
        except OSError:
            self.disk.set(f"{disk_name()}空间暂时无法读取")

    def scan(self):
        if self.app.busy:
            return
        if self._pending_open is not None:
            self.after_cancel(self._pending_open)
            self._pending_open = None
        self.scanning = True
        self.items, self.selected, self.icons = [], set(), {}
        self.render()
        self.heading.set("正在扫描可搬家的软件…")
        self.start_page_progress("正在读取软件安装信息…")
        self.note.set("正在计算软件占用空间，只读取信息。可以取消并保留已找到的结果。")
        try:
            info = disk_snapshot()
            self.disk.set(f"{disk_name(info)}共 {size_text(info['total'])}，可用 {size_text(info['free'])}")
        except OSError:
            self.disk.set(f"{disk_name()}空间暂时无法读取")
        def work():
            return migration.scan(self.app.cancel, self.app.report_progress,
                lambda items: self.app.events.put(("batch", (self.consume, items))),
                on_progress=self.report_stage_progress)
        def done(result):
            self.scanning = False
            self.scan_summary = result
            self.items = result["items"]
            self.render()
            self.finish_page_progress("扫描已取消，保留已找到的结果" if result["cancelled"] else
                f"扫描完成：发现 {sum(not item.blocked for item in self.items)} 款可搬软件", not result["cancelled"])
            count = sum(result["skipped"].values())
            self.note.set(("扫描已取消，显示已完成部分。" if result["cancelled"] else "扫描完成。") +
                "请先退出待搬家的软件；复制校验后保留原路径入口，并清除已校验的旧副本。" +
                (f" 已排除 {result.get('excluded_services', 0)} 个含系统服务的软件。" if result.get("excluded_services") else "") +
                (f" {count} 个目录无法完整读取或含链接，已跳过。" if count else ""))
        def failed(error):
            self.scanning = False
            self.render()
            self.finish_page_progress("扫描未完成", False)
            self.note.set(f"扫描未完成：{error}")
        self.app.start("扫描可搬家的软件", work, done, failed)

    def consume(self, items):
        self.items.extend(items)
        # Append early results without waiting for the slowest installed app.
        if self.empty.winfo_manager():
            self.empty.pack_forget()
        for item in items:
            row = SoftwareRow(self.inner, self, item)
            row.pack(fill="x")
            self.rows[item.key] = row
        self.update_summary()

    def render(self):
        for row in self.rows.values():
            row.destroy()
        self.rows = {}
        self.empty.pack_forget()
        for item in sorted(self.items, key=lambda value: getattr(value, self.sort_key), reverse=self.descending):
            row = SoftwareRow(self.inner, self, item)
            row.pack(fill="x")
            self.rows[item.key] = row
        if not self.items:
            self.empty.configure(text="正在读取已安装软件…" if self.scanning else "未发现可搬家的软件。可检查权限，或重新扫描。")
            self.empty.pack(fill="x")
        self.after_idle(lambda: self.canvas.yview_moveto(0))
        self.update_summary()

    def update_summary(self):
        chosen = [item for item in self.items if item.key in self.selected and not item.blocked]
        self.heading.set(f"发现可搬家软件 {sum(not item.blocked for item in self.items)} 款，已选择 {len(chosen)} 款"
                         if self.scan_summary or self.scanning or self.items else "软件搬家")
        self.selection.set(f"已选择 {len(chosen)} 项，可释放空间：{size_text(sum(item.size for item in chosen))}")
        allowed = {item.key for item in self.items if not item.blocked}
        if self.scan_summary or self.scanning or self.items:
            self.app.set_nav_metric("migrate", sum(item.size for item in self.items if not item.blocked))
        self.select_all_value.set(bool(allowed) and allowed <= self.selected)
        self.move_button.set_enabled(bool(chosen) and not self.app.busy)
        for row in self.rows.values():
            row.value.set(row.item.key in self.selected)
            row.action.set_enabled(not row.item.blocked and not self.app.busy)
            row.path.configure(fg=CORAL if row.item.blocked else MUTED)
            if row.item.blocked:
                row.path.configure(text=row.item.blocked + " · " + row.item.source)

    def select_all(self):
        if self.app.busy:
            self.update_summary()
            return
        self.selected = {item.key for item in self.items if not item.blocked} if self.select_all_value.get() else set()
        self.update_summary()

    def sort(self, key):
        self.descending = not self.descending if self.sort_key == key else True
        self.sort_key = key
        self.render()

    def choose_destination(self):
        if self.app.busy:
            return
        value = filedialog.askdirectory(parent=self, title="选择其他磁盘上的软件搬家目录", initialdir=self.destination.get() or None)
        if value:
            try:
                migration.save_destination(value)
            except OSError as exc:
                self.note.set(f"无法保存目标位置：{exc}")
                return
            self.destination.set(value)
            self.note.set("已更换目标位置；点击立即搬家后会检查磁盘格式、剩余空间及软件运行状态。")

    def move_selected(self):
        self.move_items([item for item in self.items if item.key in self.selected and not item.blocked])

    def move_items(self, items):
        if self.app.busy or not items:
            return
        destination = self.destination.get()
        if not destination:
            self.choose_destination()
            destination = self.destination.get()
            if not destination:
                return
        self.note.set("正在复制和校验；原路径切换期间请勿启动相关软件。")
        self.start_page_progress(f"准备搬家，共 {len(items)} 款软件…")
        def work():
            results = []
            for index, item in enumerate(items):
                if self.app.cancel.is_set():
                    break
                try:
                    record = migration.move(item, destination, self.app.cancel, self.app.report_progress,
                        on_progress=lambda data: self.report_stage_progress(dict(data, item_index=index+1, item_total=len(items))))
                    results.append(dict(item=item, record=record, ok=record["ok"]))
                except (OSError, ValueError, InterruptedError) as exc:
                    results.append(dict(item=item, ok=False, error=str(exc)))
            return results
        def done(results):
            successful = {entry["item"].key for entry in results if entry["ok"]}
            released = sum(entry.get("record", {}).get("released", 0) for entry in results)
            self.items = [item for item in self.items if item.key not in successful]
            self.selected.difference_update(successful)
            self.render()
            errors = [entry.get("error") or entry.get("record", {}).get("error") for entry in results]
            errors = [value for value in errors if value]
            self.finish_page_progress("搬家已取消" if self.app.cancel.is_set() else
                f"搬家完成：成功 {len(successful)} 款" + (f"，{len(errors)} 项需处理" if errors else ""),
                not self.app.cancel.is_set() and not errors)
            self.note.set(f"搬家{'已取消' if self.app.cancel.is_set() else '完成'}：成功 {len(successful)} 款，释放 {size_text(released)}。" +
                          (f" {len(errors)} 项需处理：{errors[0]}。可查看搬家记录。" if errors else "原路径可继续访问。"))
            self.app.home_page.refresh()
        def failed(error):
            self.finish_page_progress("搬家未完成", False)
            self.note.set(f"搬家未完成：{error}")
        self.app.start("软件搬家及完整校验", work, done, failed)
        self.update_summary()

    def show_history(self):
        window, created = single_window(self, "_history_window", "软件搬家记录")
        if not created:
            return window
        window.geometry("940x500")
        window.minsize(760, 420)
        window.configure(bg=WHITE)
        tk.Label(window, text="软件搬家记录", bg=WHITE, fg=INK, font=(FONT, 17)).pack(anchor="w", padx=20, pady=18)
        tree = ttk.Treeview(window, columns=("name", "time", "status", "released"), show="headings", selectmode="browse")
        for key, title, width in (("name", "软件", 220), ("time", "时间", 155), ("status", "状态", 280), ("released", "释放空间", 110)):
            tree.heading(key, text=title)
            tree.column(key, width=width)
        tree.pack(fill="both", expand=True, padx=20)
        detail = tk.StringVar(value="选择记录查看原位置和目标；还原会复制目标中的当前数据，目标副本保留。")
        tk.Label(window, textvariable=detail, bg=WHITE, fg=MUTED, font=(FONT, 9), justify="left", wraplength=870).pack(anchor="w", padx=20, pady=12)
        entries = []
        def reload():
            try:
                entries[:] = migration.records()
            except OSError as exc:
                detail.set(f"记录读取失败：{exc}")
                return
            tree.delete(*tree.get_children())
            for index, record in enumerate(entries):
                tree.insert("", "end", iid=str(index), values=(record["name"], datetime.fromtimestamp(record["time"]).strftime("%Y-%m-%d %H:%M"), record["status"], size_text(record.get("released", 0))))
        def selected(event=None):
            if tree.selection():
                record = entries[int(tree.selection()[0])]
                detail.set(f"原位置：{record['source']}\n目标：{record['target']}" + (f"\n{record['error']}" if record.get("error") else ""))
        tree.bind("<<TreeviewSelect>>", selected)
        def restore():
            if self.app.busy or not tree.selection():
                return
            record = entries[int(tree.selection()[0])]
            def done(result):
                if window.winfo_exists():
                    reload()
                    detail.set("已还原当前数据到原位置；目标副本保留。")
                self.note.set("软件已还原，可重新扫描。")
                self.app.home_page.refresh()
            self.app.start("校验并还原软件", lambda: migration.restore(record, self.app.cancel), done)
        ttk.Button(window, text="还原选中软件", command=restore).pack(anchor="e", padx=20, pady=(0, 15))
        window.reload_records = reload
        reload()
        return window
