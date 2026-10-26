"""Installed software inventory and a serial, recoverable uninstall workflow."""
from __future__ import annotations

import base64
import math
import re
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.localization.i18n import tr, display_name
from tkinter import font
from cplus_cleaner.localization.localized_tk import ttk

from cplus_cleaner.features.uninstall import software_uninstall as backend
from cplus_cleaner.ui.desktop import WHITE, INK, MUTED, MINT, PALE, LINE, CORAL, FONT, PillButton, size_text, disk_snapshot, disk_name
from cplus_cleaner.ui.ui_widgets import Checkbutton, MetricLabel


def error_text(errors):
    errors = errors or []
    return "；".join(str(error) for error in errors[:3]) + (f"；另有{len(errors)-3}项" if len(errors) > 3 else "")


class SoftwareRow(tk.Frame):
    HEIGHT = 46

    def __init__(self, parent, page, item):
        super().__init__(parent, bg=WHITE, height=self.HEIGHT)
        self.page, self.item = page, item
        self.grid_propagate(False)
        self.configure_columns(self)
        self.rowconfigure(0, weight=1)
        self.value = tk.BooleanVar(value=item.key in page.selected)
        self.checkbox = Checkbutton(self, variable=self.value, command=self.toggle, bg=WHITE)
        self.checkbox.grid(row=0, column=0)
        image = page.item_icon(item)
        self.icon = tk.Label(self, image=image or "", bg=WHITE)
        self.icon.grid(row=0, column=1, padx=(0, 5))
        self.name = tk.Label(self, text=display_name(item.name), bg=WHITE, fg=INK, font=(FONT, 10), width=1, anchor="w")
        self.name.grid(row=0, column=2, sticky="ew", padx=(3, 8))
        self.size = tk.Label(self, text=size_text(item.size), bg=WHITE, fg=MUTED, font=(FONT, 9))
        self.size.grid(row=0, column=3)
        self.date = tk.Label(self, text=page.date_text(item.install_time), bg=WHITE, fg=MUTED, font=(FONT, 9))
        self.date.grid(row=0, column=4)
        self.action = PillButton(self, "卸载", lambda: page.uninstall_items([item]), width=88, height=30)
        self.action.grid(row=0, column=5, padx=(7, 0))
        tk.Frame(self, bg=LINE, height=1).grid(row=1, column=0, columnspan=6, sticky="ew")
        self.bind("<Configure>", self.fit)
        self.name.bind("<Configure>", self.fit)
        self.refresh()

    @staticmethod
    def configure_columns(widget):
        for column, width in ((0, 28), (1, 36), (3, 85), (4, 104), (5, 100)):
            widget.columnconfigure(column, minsize=width)
        widget.columnconfigure(2, weight=1, minsize=110)

    def fit(self, event=None):
        width = max(25, self.grid_bbox(column=2, row=0)[2] - 12)
        self.name.fit_text(display_name(self.item.name), width)

    def refresh(self):
        state = self.page.states.get(self.item.key, "ready")
        captions = {"queued": "等待卸载", "running": "卸载中…", "failed": "重新卸载",
                    "cancelled": "重新卸载", "pending": "待扫描"}
        self.action.caption = "不可卸载" if getattr(self.item, "blocked", "") else captions.get(state, "卸载")
        available = self.page.selectable(self.item) and not self.page.processing
        self.action.set_enabled(available)
        self.checkbox.configure(state="normal" if available else "disabled")
        self.value.set(self.item.key in self.page.selected)

    def toggle(self):
        if self.page.processing or not self.page.selectable(self.item):
            self.value.set(self.item.key in self.page.selected)
            return
        if self.value.get():
            self.page.selected.add(self.item.key)
        else:
            self.page.selected.discard(self.item.key)
        self.page.update_summary()

    def destroy(self):
        super().destroy()
        # A later worker GC must not own the last reference to a Tcl variable.
        self.value = None


class ResidueDialog(tk.Toplevel):
    """The retained manifest is the only input allowed into residual cleanup."""
    def __init__(self, page, item, manifest, callback):
        super().__init__(page)
        self.page, self.item, self.manifest = page, item, manifest
        self._callback, self._finished, self.cleaning = callback, False, False
        self._close_requested = False
        self.title("软件卸载残留")
        self.geometry("710x430")
        self.minsize(570, 320)
        self.configure(bg=WHITE)
        self.transient(page.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self.skip)
        self.bind("<Destroy>", self._destroyed, add="+")
        tk.Label(self, text=f"{item.name} 已卸载，发现以下残留", bg=WHITE, fg=INK,
                 font=(FONT, 12), anchor="w").pack(fill="x", padx=22, pady=(18, 8))
        files, registry = manifest.get("files", []), manifest.get("registry", [])
        allowed = sum(not entry.get("blocked") for entry in files + registry)
        blocked = len(files) + len(registry) - allowed
        self.note = tk.StringVar(value=f"可清理 {allowed} 项" + (f"，另有 {blocked} 项受保护，清理时跳过。" if blocked else ""))
        self.summary = MetricLabel(self, textvariable=self.note, bg=WHITE, fg=MUTED, font=(FONT, 9))
        self.summary.pack(fill="x", padx=22, pady=(0, 10))
        bottom = tk.Frame(self, bg=WHITE)
        bottom.pack(side="bottom", fill="x", padx=22, pady=16)
        self.clean_button = PillButton(bottom, "清理残留", self.clean, width=132, height=36)
        self.clean_button.pack(side="right")
        self.clean_button.set_enabled(bool(allowed))
        self.skip_button = PillButton(bottom, "跳过", self.skip, width=110, height=36)
        self.skip_button.pack(side="right", padx=10)
        area = tk.Frame(self, bg=WHITE)
        area.pack(fill="both", expand=True, padx=22)
        self.tree = ttk.Treeview(area, show="tree", selectmode="browse")
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.tag_configure("blocked", foreground=MUTED)
        self.tree.column("#0", minwidth=400, width=620)
        for kind, title, records in (("files", "残留文件", files), ("registry", "残留注册表", registry)):
            if not records:
                continue
            group = self.tree.insert("", "end", iid=kind, text=f"{title}（{len(records)} 项）", open=True)
            for index, record in enumerate(records):
                label = str(record.get("path", ""))
                if kind == "registry":
                    label = str(record.get("root", "")) + "\\" + label
                reason = record.get("blocked")
                if reason:
                    label += "  （受保护：" + str(reason) + "）"
                self.tree.insert(group, "end", iid=f"{kind}-{index}", text=label, tags=("blocked",) if reason else ())

    def _destroyed(self, event):
        if event.widget is self:
            if self.page._residue_window is self:
                self.page._residue_window = None
            self._finish()
            self.note = None

    def _finish(self):
        if self._finished:
            return
        self._finished = True
        callback, self._callback = self._callback, None
        if callback:
            callback()

    def skip(self):
        if self.cleaning:
            self._close_requested = True
            self.page.app.cancel.set()
            self.note.set("正在停止残留清理，完成当前操作后关闭…")
            return
        self.destroy()

    def clean(self):
        if self.cleaning or self.page.app.busy:
            return
        manifest = dict(self.manifest)
        manifest["files"] = [entry for entry in manifest.get("files", []) if not entry.get("blocked")]
        manifest["registry"] = [entry for entry in manifest.get("registry", []) if not entry.get("blocked")]
        if not manifest["files"] and not manifest["registry"]:
            self.skip()
            return
        self.cleaning = True
        self.clean_button.set_enabled(False)
        self.skip_button.set_enabled(False)
        self.note.set("正在清理所列未受保护的残留…")

        def work():
            try:
                return backend.clean_residue(manifest, cancel=self.page.app.cancel,
                                             progress=self.page.report_progress)
            except Exception as error:
                return {"deleted": 0, "errors": [str(error)], "cancelled": False}

        def done(result):
            self.cleaning = False
            errors = error_text(result.get("errors"))
            if errors:
                self.page.errors[self.item.key] = f"{self.item.name} 残留清理：{errors}"
            self.page.note.set("残留清理完成" + ("：" + errors if errors else ""))
            if self.page.app.cancel.is_set() or result.get("cancelled"):
                self.page._queue_cancelled = True
            self.destroy()

        def failed(error):
            done({"deleted": 0, "errors": [error]})

        self.page.app.start("清理卸载残留", work, done, failed)


class SoftwareUninstallPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=WHITE)
        self.app = app
        self.apps, self.rows, self.icons, self.selected, self.states = [], {}, {}, set(), {}
        self.results, self.queue, self.errors = [], [], {}
        self.scanning = self.queue_active = self._loaded = False
        self._pending_open = self._queue_after = self._residue_window = None
        self._queue_cancelled = False
        self.sort_key, self.descending = "install_time", True
        self.heading = tk.StringVar(value="共 0 款软件，占用 0KB，已选择 0KB")
        self.disk = tk.StringVar(value="")
        self.note = tk.StringVar(value="正在准备软件列表…")
        self.search = tk.StringVar()
        self.select_all_value = tk.IntVar(value=0)
        header = tk.Frame(self, bg=WHITE)
        header.pack(fill="x", padx=25, pady=(24, 14))
        header.columnconfigure(0, weight=1)
        actions = tk.Frame(header, bg=WHITE)
        actions.grid(row=0, column=1, rowspan=2, sticky="ne", padx=(14, 0))
        self.scan_button = PillButton(actions, "扫描", self.load, width=142, height=39)
        self.scan_button.pack(side="left", padx=(0, 12))
        self.uninstall_button = PillButton(actions, "一键卸载", self.uninstall_selected, width=142, height=39)
        self.uninstall_button.pack(side="left")
        self.heading_label = MetricLabel(header, textvariable=self.heading, bg=WHITE, fg=INK, font=(FONT, 14))
        self.heading_label.grid(row=0, column=0, sticky="nw", pady=(3, 8))
        tk.Label(header, textvariable=self.disk, bg=WHITE, fg=MUTED, anchor="w", font=(FONT, 9)).grid(row=1, column=0, sticky="nw")
        def fit_header(event):
            available = max(240, event.width - actions.winfo_reqwidth() - 14)
            self.heading_label.configure(wraplength=available, font=(FONT, 13 if available < 440 else 14))
        header.bind("<Configure>", fit_header)
        search_frame = tk.Frame(self, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        search_frame.pack(fill="x", padx=25, pady=(0, 12))
        tk.Label(search_frame, text="⌕", bg=WHITE, fg=MUTED, font=(FONT, 15)).pack(side="left", padx=8)
        self.search_entry = tk.Entry(search_frame, textvariable=self.search, relief="flat", bg=WHITE, fg=INK,
                                     font=(FONT, 10), bd=0)
        self.search_entry.pack(fill="x", expand=True, padx=(0, 10), ipady=7)
        self.search_placeholder = tk.Label(self.search_entry, text="搜索软件或应用", bg=WHITE, fg="#aebdc0", font=(FONT, 10), cursor="xterm")
        self.search_placeholder.place(x=0, rely=.5, anchor="w")
        self.search_placeholder.bind('<Button-1>', lambda event: self.search_entry.focus_set())
        self.search_entry.bind('<FocusIn>', lambda event: self.search_placeholder.place_forget())
        self.search_entry.bind('<FocusOut>', lambda event: self._update_placeholder())
        self._search_trace = self.search.trace_add("write", self._search_changed)
        self.table_header = tk.Frame(self, bg="#f2f7fa")
        self.table_header.pack(fill="x", padx=(25, 41))
        SoftwareRow.configure_columns(self.table_header)
        self.select_all_button = Checkbutton(self.table_header, variable=self.select_all_value,
            command=self.select_all, tristatevalue=-1, bg="#f2f7fa")
        self.select_all_button.grid(row=0, column=0, pady=5)
        tk.Label(self.table_header, text="软件名称", bg="#f2f7fa", fg=MUTED, anchor="w", font=(FONT, 9)).grid(row=0, column=1, columnspan=2, sticky="ew")
        tk.Label(self.table_header, text="大小", bg="#f2f7fa", fg=MUTED, font=(FONT, 9)).grid(row=0, column=3)
        self.date_button = tk.Button(self.table_header, text="安装时间 ↓", command=lambda: self.sort("install_time"),
            bg="#f2f7fa", fg=MUTED, bd=0, relief="flat", font=(FONT, 9))
        self.date_button.grid(row=0, column=4)
        tk.Label(self.table_header, text="操作", bg="#f2f7fa", fg=MUTED, font=(FONT, 9)).grid(row=0, column=5)
        tk.Label(self, textvariable=self.note, bg=WHITE, fg=MUTED, anchor="w", justify="left",
                 wraplength=700, font=(FONT, 9)).pack(side="bottom", fill="x", padx=25, pady=(7, 10))
        area = tk.Frame(self, bg=WHITE)
        area.pack(fill="both", expand=True, padx=25)
        self.canvas = tk.Canvas(area, bg=WHITE, bd=0, highlightthickness=0)
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=WHITE)
        self.inner_window = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.inner_window, width=event.width))
        self.inner.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.empty = tk.Label(self.inner, text="正在准备软件列表…", bg=WHITE, fg=MUTED, font=(FONT, 11), pady=48)
        self.empty.pack(fill="x")
        self._wheel_binding = self.bind_all("<MouseWheel>", self.mousewheel, add="+")
        self.update_summary()

    @property
    def processing(self):
        return self.scanning or self.queue_active or self.app.busy

    def _update_placeholder(self):
        if not self.search.get() and self.focus_get() is not self.search_entry:
            self.search_placeholder.place(x=0, rely=.5, anchor="w")
        else:
            self.search_placeholder.place_forget()

    def _search_changed(self, *args):
        self._update_placeholder()
        self.render()

    @property
    def items(self):
        return self.apps

    @staticmethod
    def date_text(value):
        digits = re.sub(r"\D", "", str(value or ""))
        return f"{digits[:4]}/{digits[4:6]}/{digits[6:8]}" if len(digits) >= 8 else (str(value) if value else "未知")

    def item_icon(self, item):
        if item.key not in self.icons and item.icon:
            try:
                image = tk.PhotoImage(master=self, data=base64.b64encode(item.icon))
                factor = max(1, math.ceil(max(image.width(), image.height()) / 40))
                self.icons[item.key] = image.subsample(factor) if factor > 1 else image
            except tk.TclError:
                self.icons[item.key] = None
        return self.icons.get(item.key) or self.app.images.navigation("uninstall")

    def selectable(self, item):
        return not getattr(item, "blocked", "") and self.states.get(item.key, "ready") not in {"queued", "running", "success", "pending"}

    def visible_apps(self):
        query = self.search.get().strip().casefold()
        result = [item for item in self.apps if query in item.name.casefold() or query in tr(display_name(item.name)).casefold()]
        if self.sort_key == "install_time":
            key = lambda item: (re.sub(r"\D", "", str(item.install_time or "")), item.name.casefold())
        elif self.sort_key == "size":
            key = lambda item: (item.size, item.name.casefold())
        else:
            key = lambda item: item.name.casefold()
        return sorted(result, key=key, reverse=self.descending)

    def sort(self, key):
        self.descending = not self.descending if self.sort_key == key else True
        self.sort_key = key
        self.date_button.configure(text="安装时间 " + ("↓" if self.descending else "↑"))
        self.render()

    def mousewheel(self, event):
        target = self.winfo_containing(event.x_root, event.y_root)
        while target is not None:
            if target == self.canvas:
                self.canvas.yview_scroll(int(-event.delta / 120), "units")
                return
            target = getattr(target, "master", None)

    def render(self):
        for row in self.rows.values():
            row.destroy()
        self.rows = {}
        visible = self.visible_apps()
        if visible:
            self.empty.pack_forget()
        else:
            self.empty.configure(text="正在扫描软件…" if self.scanning else "没有找到匹配的软件" if self.search.get().strip() else "暂无可卸载软件")
            self.empty.pack(fill="x")
        for item in visible:
            row = SoftwareRow(self.inner, self, item)
            row.pack(fill="x")
            self.rows[item.key] = row
        self.canvas.yview_moveto(0)
        self.update_summary()

    def update_summary(self):
        total = sum(item.size for item in self.apps)
        selected_size = sum(item.size for item in self.apps if item.key in self.selected)
        self.heading.set(f"共 {len(self.apps)} 款软件，占用 {size_text(total)}，已选择 {size_text(selected_size)}")
        if hasattr(self.app, "set_nav_metric"):
            self.app.set_nav_metric("uninstall", len(self.apps), "款")
        visible = [item for item in self.visible_apps() if self.selectable(item)]
        checked = sum(item.key in self.selected for item in visible)
        self.select_all_value.set(1 if visible and checked == len(visible) else -1 if checked else 0)
        available = not self.processing
        self.select_all_button.configure(state="normal" if available and visible else "disabled")
        self.uninstall_button.set_enabled(available and any(item.key in self.selected and self.selectable(item) for item in self.apps))
        self.scan_button.set_enabled(available)
        for row in self.rows.values():
            row.refresh()

    def select_all(self):
        if self.processing:
            self.update_summary()
            return
        visible = [item for item in self.visible_apps() if self.selectable(item)]
        if self.select_all_value.get():
            self.selected.update(item.key for item in visible)
        else:
            self.selected.difference_update(item.key for item in visible)
        self.update_summary()

    def on_open(self):
        self.update_summary()
        if (self._loaded and not self.app.busy) or self._pending_open is not None or self.scanning:
            return

        def begin():
            self._pending_open = None
            if not self.winfo_exists() or getattr(self.app, "current_page", "uninstall") != "uninstall":
                return
            if self.app.busy:
                self._pending_open = self.after(150, begin)
            elif self._loaded:
                self.update_summary()
            else:
                self.load()

        self._pending_open = self.after_idle(begin)

    def load(self):
        if self._pending_open is not None:
            self.after_cancel(self._pending_open)
            self._pending_open = None
        if self.processing:
            return
        self.scanning = True
        self.apps, self.selected, self.states, self.results, self.errors = [], set(), {}, [], {}
        self.icons.clear()
        self.search.set("")
        self.render()
        self.note.set("正在读取已安装的软件…")
        try:
            info = disk_snapshot()
            self.disk.set(f"{disk_name(info)}共 {size_text(info['total'])}，可用 {size_text(info['free'])}")
        except OSError:
            self.disk.set("")

        def work():
            return backend.scan(cancel=self.app.cancel, progress=self.report_progress)

        def done(result):
            self.scanning = False
            self._loaded = True
            self.apps = list(result.get("items", []))
            self.states = {item.key: "ready" for item in self.apps}
            self.render()
            errors = error_text(result.get("errors"))
            self.note.set(("扫描已取消，显示已找到的软件。" if result.get("cancelled") else "扫描完成。") + (" " + errors if errors else ""))

        def failed(error):
            self.scanning = False
            self.note.set(f"扫描未完成：{error}")
            self.update_summary()

        self.app.start("扫描已安装软件", work, done, failed)

    scan = load

    def report_progress(self, data):
        message = str(data.get("message", "正在处理…")) if isinstance(data, dict) else str(data)
        self.app.report_progress(message)
        self.app.events.put(("batch", (self.note.set, message)))

    def uninstall_selected(self):
        self.uninstall_items([item for item in self.apps if item.key in self.selected])

    def uninstall_items(self, items):
        if self.processing:
            return
        requested, seen = [], set()
        inventory = {item.key: item for item in self.apps}
        for item in items:
            current = inventory.get(item.key)
            if current is not None and current.key not in seen and self.selectable(current):
                requested.append(current)
                seen.add(current.key)
        if not requested:
            return
        # A cancellation belongs to the previous task. This explicit new action
        # starts a fresh queue before _advance_queue checks the shared event.
        self.app.cancel.clear()
        self.queue = requested
        self.queue_active, self._queue_cancelled = True, False
        self.results = []
        for item in requested:
            self.states[item.key] = "queued"
            self.selected.discard(item.key)
        self.update_summary()
        self._advance_queue()

    def _schedule_next(self):
        if self._queue_after is None:
            self._queue_after = self.after_idle(self._advance_queue)

    def _advance_queue(self):
        self._queue_after = None
        if not self.winfo_exists():
            return
        if self.app.cancel.is_set():
            self._queue_cancelled = True
        if self.app.busy:
            self._queue_after = self.after(150, self._advance_queue)
            return
        if self._queue_cancelled:
            for waiting in self.queue:
                self.states[waiting.key] = "cancelled"
            self.queue = []
        if not self.queue:
            self.queue_active = False
            counts = {status: sum(result.get("status") == status for result in self.results)
                      for status in ("success", "failed", "cancelled", "pending")}
            summary = ("卸载任务已取消；" if self._queue_cancelled else "卸载队列已处理完成；")
            summary += f"完成 {counts['success']} 款，失败 {counts['failed']} 款，取消 {counts['cancelled']} 款，待重扫 {counts['pending']} 款。"
            if self.errors:
                summary += " " + "；".join(list(self.errors.values())[-3:])
            self.note.set(summary)
            self.update_summary()
            return
        item = self.queue.pop(0)
        self.states[item.key] = "running"
        self.note.set(f"正在卸载 {item.name}…")
        self.update_summary()

        def work():
            try:
                return backend.uninstall(item, cancel=self.app.cancel, progress=self.report_progress)
            except Exception as error:
                return {"key": item.key, "name": item.name, "status": "failed", "error": str(error)}

        def done(result):
            status = result.get("status", "failed")
            if status not in {"success", "cancelled", "failed", "pending"}:
                status = "failed"
            self.states[item.key] = status
            self.results.append(dict(result, status=status))
            self._queue_cancelled = self._queue_cancelled or self.app.cancel.is_set() or bool(result.get("cancelled"))
            if result.get("error"):
                self.errors[item.key] = f"{item.name}：{result['error']}"
            if status == "success":
                self.apps = [entry for entry in self.apps if entry.key != item.key]
            self.render()
            self.note.set(f"{item.name}：" + {"success": "卸载完成", "cancelled": "已取消", "failed": "卸载失败", "pending": "卸载状态待核实，请重新扫描"}[status] + (" · " + str(result["error"]) if result.get("error") else ""))
            if status == "success" and not self._queue_cancelled:
                self._scan_residue(item)
            else:
                self._schedule_next()

        def failed(error):
            done({"key": item.key, "name": item.name, "status": "failed", "error": error})

        self.app.start(f"卸载 {item.name}", work, done, failed)

    def _scan_residue(self, item):
        self.note.set(f"{item.name} 已卸载，正在扫描残留…")

        def work():
            try:
                return backend.scan_residue(item, cancel=self.app.cancel, progress=self.report_progress)
            except Exception as error:
                return {"files": [], "registry": [], "errors": [str(error)]}

        def done(manifest):
            if self.app.cancel.is_set():
                self._queue_cancelled = True
                self._schedule_next()
                return
            if manifest.get("files") or manifest.get("registry"):
                self._residue_window = ResidueDialog(self, item, manifest, self._schedule_next)
            else:
                errors = error_text(manifest.get("errors"))
                if errors:
                    self.errors[item.key] = f"{item.name} 残留扫描：{errors}"
                    self.note.set(f"{item.name} 残留扫描未完成：{errors}")
                self._schedule_next()

        self.app.start(f"扫描 {item.name} 的卸载残留", work, done,
                       lambda error: done({"files": [], "registry": [], "errors": [error]}))

    def destroy(self):
        for attribute in ("_pending_open", "_queue_after"):
            timer = getattr(self, attribute, None)
            if timer is not None:
                self.after_cancel(timer)
                setattr(self, attribute, None)
        if self._search_trace:
            self.search.trace_remove("write", self._search_trace)
            self._search_trace = None
        if self._wheel_binding:
            self._root()._unbind(("bind", "all", "<MouseWheel>"), self._wheel_binding)
            self._wheel_binding = None
        if self._residue_window is not None:
            self._residue_window._callback = None
            self._residue_window.destroy()
        super().destroy()
        self.icons.clear()
        for attribute in ("heading", "disk", "note", "search", "select_all_value"):
            setattr(self, attribute, None)

# Purpose: App Uninstaller page, search and selection
