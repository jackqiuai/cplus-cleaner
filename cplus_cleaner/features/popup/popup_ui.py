"""Original-style, grouped popup rules over the local verified blocker."""
from __future__ import annotations

import base64
import math
import queue
import threading
import time
from cplus_cleaner.localization import localized_tk as tk
from tkinter import font
from cplus_cleaner.localization.localized_tk import ttk

from cplus_cleaner.features.popup import popup as backend
from cplus_cleaner.localization.i18n import original, tr
from cplus_cleaner.ui.desktop import WHITE, INK, MUTED, LINE, FONT, PillButton, text_button
from cplus_cleaner.ui.ui_widgets import MetricLabel, single_window


def position_text(value, source='huorong'):
    return {-1:'不限位置',0:'近似全屏',1:'左上窗口',2:'左下窗口',
            3:'中间窗口',4:'右上窗口',5:'右下窗口'}.get(value,'未知位置')


class RuleSwitch(tk.Canvas):
    """40 by 18 switch from the original Element UI CSS; no Tk variable."""
    def __init__(self, parent, command, value=False, unavailable_command=None):
        super().__init__(parent, width=40, height=18, bg=WHITE, bd=0,
                         highlightthickness=0, cursor="hand2", takefocus=True)
        self.command, self.value, self.enabled = command, bool(value), True
        self.unavailable_command = unavailable_command
        self.bind("<Button-1>", lambda event: self.invoke())
        self.bind("<space>", lambda event: self.invoke())
        self.bind("<Return>", lambda event: self.invoke())
        self.paint()

    def paint(self):
        self.delete("all")
        color = "#51db9e" if self.value else "#8fb8c0"
        if not self.enabled:
            color = "#c9dedf"
        self.create_oval(0, 0, 18, 18, fill=color, outline="")
        self.create_rectangle(9, 0, 31, 18, fill=color, outline="")
        self.create_oval(22, 0, 40, 18, fill=color, outline="")
        x = 23 if self.value else 1
        self.create_oval(x, 1, x+16, 17, fill=WHITE, outline="")
        self.configure(cursor="hand2" if self.enabled or self.unavailable_command else "arrow")

    def set(self, value, enabled=True):
        self.value, self.enabled = bool(value), bool(enabled)
        self.paint()

    def invoke(self):
        if self.enabled:
            return self.command(not self.value)
        if self.unavailable_command:
            return self.unavailable_command()


class PopupRow(tk.Frame):
    def __init__(self, parent, page, item):
        super().__init__(parent, bg=WHITE)
        self.page, self.item = page, item
        self.rule_rows = {}
        self.header = tk.Frame(self, bg=WHITE, height=50)
        self.header.pack(fill="x")
        self.header.grid_propagate(False)
        self.configure_columns(self.header)
        self.header.rowconfigure(0, weight=1)
        source = tk.Frame(self.header, bg=WHITE)
        source.grid(row=0, column=0, sticky="ew", padx=(18, 6))
        self.icon = tk.Label(source, image=page.item_icon(item) or "", bg=WHITE)
        self.icon.pack(side="left", padx=(0, 9))
        self.display_name = original(item.name)
        self.name = tk.Label(source, text=self.display_name, width=1, anchor="w", bg=WHITE,
                             fg="#6c8787", font=(FONT, 10))
        self.name.pack(side="left", fill="x", expand=True)
        self.name.bind("<Configure>", self.fit_name)
        self.positions = tk.Label(self.header, text=f"{len(item.rules)}项弹窗", bg=WHITE,
                                  fg="#6c8787", font=(FONT, 9))
        self.positions.grid(row=0, column=1)
        self.count = tk.Label(self.header, bg=WHITE, fg="#6c8787", font=(FONT, 9))
        self.count.grid(row=0, column=2)
        actions = tk.Frame(self.header, bg=WHITE)
        actions.grid(row=0, column=3, sticky="e", padx=(0, 8))
        self.switch = RuleSwitch(actions, lambda value: page.set_software(item, value))
        self.switch.pack(side="left")
        self.arrow = tk.Canvas(actions, width=24, height=22, bg=WHITE, bd=0,
                               highlightthickness=0, cursor="hand2", takefocus=True)
        self.arrow.pack(side="left", padx=(6, 0))
        # Tk child events do not bubble to their parent frames. Bind each
        # header surface, leaving the independent group switch untouched.
        for widget in (self.header, source, self.icon, self.name, self.positions,
                       self.count, actions, self.arrow):
            widget.configure(cursor="hand2")
            widget.bind("<Button-1>", self.toggle_expansion)
        self.header.configure(takefocus=True)
        for widget in (self.header, self.arrow):
            widget.bind("<space>", self.toggle_expansion)
            widget.bind("<Return>", self.toggle_expansion)
        self.children_area = tk.Frame(self, bg=WHITE)
        original_icon = page.item_icon(item)
        self.rule_icon = None
        if original_icon:
            largest = max(original_icon.width(), original_icon.height())
            divisor = math.gcd(largest, 16)
            self.rule_icon = original_icon.zoom(16//divisor).subsample(largest//divisor)
        for rule in item.rules:
            row = tk.Frame(self.children_area, bg=WHITE, height=36)
            row.pack(fill="x")
            row.grid_propagate(False)
            self.configure_columns(row)
            row.rowconfigure(0, weight=1)
            tk.Label(row, image=self.rule_icon or "", bg=WHITE).grid(
                row=0, column=0, sticky="w", padx=(40, 0))
            label = tk.Label(row, text=rule.label, width=1, anchor="w", bg=WHITE,
                             fg="#6c8787", font=(FONT, 9))
            label.grid(row=0, column=0, sticky="ew", padx=(65, 10))
            label.bind("<Configure>", lambda event, label=label, text=rule.label: self.fit_label(label, text, event.width))
            tk.Label(row, text=position_text(rule.pos, rule.source), bg=WHITE, fg="#6c8787",
                     font=(FONT, 9)).grid(row=0, column=1)
            count = tk.Label(row, bg=WHITE, fg="#6c8787", font=(FONT, 9))
            count.grid(row=0, column=2)
            switch = RuleSwitch(row, lambda value, rule=rule: page.set_rule(rule, value),
                                unavailable_command=lambda rule=rule: page.open_rule_options(rule))
            switch.grid(row=0, column=3, sticky="e", padx=(0, 38))
            self.rule_rows[rule.id] = (row, switch, count)
        self.refresh()

    @staticmethod
    def configure_columns(widget):
        widget.columnconfigure(0, weight=1, minsize=120)
        for column, width in ((1, 142), (2, 112), (3, 86)):
            widget.columnconfigure(column, minsize=width)

    @staticmethod
    def fit_label(label, text, width):
        label.fit_text(text, width)

    def fit_name(self, event):
        self.fit_label(self.name, self.display_name, event.width)

    def toggle_expansion(self, event=None):
        self.page.toggle_expanded(self.item.key)
        return "break"

    def refresh(self):
        page, item = self.page, self.item
        group_enabled = page.blocker.group_enabled(item)
        self.switch.set(group_enabled, not page.scanning and any(page.blocker.rule_available(r) for r in item.rules))
        self.count.configure(text=f"{page.blocker.group_count(item)}次")
        expanded = item.key in page.expanded
        self.arrow.delete("all")
        # Exact original arrow path, translated to the 24px click target.
        points = (6, 14, 12, 8, 18, 14) if expanded else (6, 8, 12, 14, 18, 8)
        self.arrow.create_line(*points, fill="#8eb0b7", width=1)
        if expanded:
            self.children_area.pack(fill="x")
        else:
            self.children_area.pack_forget()
        for rule in item.rules:
            _, switch, count = self.rule_rows[rule.id]
            switch.set(group_enabled and page.blocker.rule_enabled(rule),
                       not page.scanning and page.blocker.rule_available(rule))
            count.configure(text=f"{page.blocker.rule_count(rule)}次")


class PopupPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=WHITE)
        self.app = app
        self.blocker = backend.Blocker()
        self.items, self.rows, self.icons, self.expanded = [], {}, {}, set()
        self.scanning = self._loaded = self._destroyed = False
        self._pending_open = self._tick_after = self._history_window = None
        self._last_error = ""
        self._poll_worker = None
        self._poll_results = queue.Queue()
        self.heading = tk.StringVar(value="发现 0 款软件 0 项广告弹窗")
        self.summary = tk.StringVar(value="告别弹窗骚扰")
        self.note = tk.StringVar(value="")
        self.progress_text = tk.StringVar(value="")
        self.progress_percent = tk.StringVar(value="")
        self.progress_value = tk.DoubleVar(value=0)
        self._scan_fraction = 0
        header = tk.Frame(self, bg=WHITE)
        header.pack(fill="x", padx=32, pady=(25, 20))
        header.columnconfigure(1, weight=1)
        self.warning_image = app.images.get("status-warning.png")
        self.warning = tk.Label(header, image=self.warning_image or "", bg=WHITE)
        self.warning.grid(row=0, column=0, rowspan=2, padx=(0, 10), sticky="nw", pady=2)
        self.heading_label = MetricLabel(header, textvariable=self.heading, bg=WHITE,
                                         fg=INK, font=(FONT, 14))
        self.heading_label.grid(row=0, column=1, sticky="nw")
        self.summary_label = MetricLabel(header, textvariable=self.summary, bg=WHITE,
                                         fg="#999999", font=(FONT, 9))
        self.summary_label.grid(row=1, column=1, sticky="nw", pady=(5, 0))
        self.actions = tk.Frame(header, bg=WHITE)
        self.actions.grid(row=0, column=2, rowspan=2, sticky="ne", padx=(12, 0))
        self.scan_button = PillButton(self.actions, "扫描", self.scan, width=150)
        self.scan_button.pack(side="left", padx=(0, 12))
        self.block_button = PillButton(self.actions, "全部拦截", self.toggle_all, width=150)
        self.block_button.pack(side="left")
        header.bind("<Configure>", self.fit_header)
        footer = tk.Frame(self, bg=WHITE)
        footer.pack(side="bottom", fill="x", padx=25, pady=(8, 14))
        self.history_button = text_button(footer, "拦截记录", self.show_history, color="#6c8787")
        self.history_button.configure(font=(FONT, 10, "underline"))
        self.history_button.pack(side="right")
        tk.Label(self, textvariable=self.note, bg=WHITE, fg=MUTED, anchor="w", justify="left",
                 font=(FONT, 9), wraplength=680).pack(side="bottom", fill="x", padx=32, pady=(5, 0))
        self.progress_frame = tk.Frame(self, bg=WHITE)
        progress_labels = tk.Frame(self.progress_frame, bg=WHITE)
        progress_labels.pack(fill="x", pady=(0, 6))
        tk.Label(progress_labels, textvariable=self.progress_text, bg=WHITE, fg=MUTED,
                 anchor="w", font=(FONT, 9)).pack(side="left", fill="x", expand=True)
        tk.Label(progress_labels, textvariable=self.progress_percent, bg=WHITE, fg="#36c894",
                 font=(FONT, 9), width=6, anchor="e").pack(side="right")
        ttk.Style(self).configure("Popup.Horizontal.TProgressbar", background="#51db9e",
                                  troughcolor="#eef5f5", borderwidth=0, thickness=6)
        self.scan_progress = ttk.Progressbar(self.progress_frame, maximum=100,
                                             variable=self.progress_value, mode="determinate",
                                             style="Popup.Horizontal.TProgressbar")
        self.scan_progress.pack(fill="x")
        self.table_header = tk.Frame(self, bg="#f2f6fa", height=32)
        self.table_header.pack(fill="x", padx=(32, 48))
        self.table_header.grid_propagate(False)
        self.table_header.rowconfigure(0, weight=1)
        PopupRow.configure_columns(self.table_header)
        for column, text in enumerate(("弹窗来源", "弹窗位置", "拦截次数", "操作")):
            tk.Label(self.table_header, text=text, bg="#f2f6fa", fg="#6c8787", font=(FONT, 9),
                     anchor="w" if column == 0 else "center").grid(
                         row=0, column=column, sticky="ew", padx=(18, 0) if column == 0 else 0)
        area = tk.Frame(self, bg=WHITE)
        area.pack(fill="both", expand=True, padx=32)
        self.canvas = tk.Canvas(area, bg=WHITE, bd=0, highlightthickness=0)
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=WHITE)
        self.inner_window = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.canvas.bind("<Configure>", self.resize_canvas)
        self.inner.bind("<Configure>", self.update_scrollregion)
        self.empty = tk.Label(self.inner, text="正在准备弹窗规则…", bg=WHITE,
                              fg="#6c8787", font=(FONT, 11), pady=85)
        self.empty.pack(fill="x")
        self._wheel_binding = self.bind_all("<MouseWheel>", self.mousewheel, add="+")
        self.update_counter()
        self.restore_saved_results()
        self._tick_after = self.after(250, self.tick)

    def fit_header(self, event):
        width = max(100, event.width-self.actions.winfo_reqwidth()-self.warning.winfo_reqwidth()-25)
        self.heading_label.configure(wraplength=width, font=(FONT, 12 if width < 350 else 14))
        self.summary_label.configure(wraplength=width)

    def resize_canvas(self, event):
        self.canvas.itemconfigure(self.inner_window, width=event.width)
        self.update_scrollregion()

    def update_scrollregion(self, event=None):
        # A region shorter than the viewport lets Tk align its bottom on an
        # upward wheel event, moving every row down into the empty space.
        content_height = self.inner.winfo_reqheight()
        viewport_height = self.canvas.winfo_height()
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(),
                                           max(content_height, viewport_height)))
        if content_height <= viewport_height:
            self.canvas.yview_moveto(0)

    def mousewheel(self, event):
        target = self.winfo_containing(event.x_root, event.y_root)
        while target is not None:
            if target == self.canvas:
                if self.inner.winfo_reqheight() > self.canvas.winfo_height():
                    self.canvas.yview_scroll(int(-event.delta/120), "units")
                else:
                    self.canvas.yview_moveto(0)
                return "break"
            target = getattr(target, "master", None)

    def item_icon(self, item):
        if item.key not in self.icons:
            image = None
            if item.icon:
                try:
                    image = tk.PhotoImage(master=self, data=base64.b64encode(item.icon))
                    # Installed icons normally arrive at 32px; preserve the aspect
                    # ratio if an original source contains a larger bitmap.
                    largest = max(image.width(), image.height())
                    if largest > 32:
                        divisor = math.gcd(largest, 32)
                        image = image.zoom(32//divisor).subsample(largest//divisor)
                except (tk.TclError, ValueError):
                    image = None
            self.icons[item.key] = image or self.app.images.navigation("popup")
        return self.icons[item.key]

    def on_open(self):
        self.update_counter()

    def restore_saved_results(self):
        try:
            items = backend.load_results()
        except (OSError, ValueError, TypeError):
            self.note.set("上次扫描结果无法读取，请点击扫描。")
            items = None
        self._loaded = items is not None
        self.items = items or []
        self.render()

    def scan(self):
        if self._destroyed or self.scanning or self.app.busy:
            return
        if self._pending_open is not None:
            self.after_cancel(self._pending_open)
            self._pending_open = None
        self.scanning = True
        self.note.set("")
        self._scan_fraction = 0
        self.progress_value.set(0)
        self.progress_frame.pack(fill="x", padx=32, pady=(0, 14), before=self.table_header)
        self.consume_scan_progress(dict(message="正在读取已安装软件信息…", total=None))
        self.update_counter()
        def work():
            return backend.scan(cancel=self.app.cancel, progress=self.app.report_progress,
                                stage_progress=lambda data: self.app.events.put(
                                    ("batch", (self.consume_scan_progress, dict(data)))))
        def done(result):
            if self._destroyed:
                return
            self.scanning = False
            self.finish_scan_progress("扫描已取消" if result.get("cancelled") else "扫描完成",
                                      complete=not result.get("cancelled"))
            self._loaded = True
            self.items = backend.visible_items(result.get("items", []))
            setting_error = ""
            cache_error = ""
            if not result.get("cancelled"):
                try:
                    backend.save_results(self.items)
                except (OSError, ValueError):
                    cache_error = "扫描结果未保存，请重新扫描。"
            try:
                self.blocker.set_items(self.items)
                if not result.get("cancelled"):
                    self.blocker.enable_detected()
            except (OSError, ValueError) as error:
                setting_error = f"自动开启设置未保存：{error}"
            self.render()
            errors = list(result.get("errors", []))
            details = f"{len(errors)}项软件信息或图标无法读取，已跳过。" if errors else ""
            if result.get("cancelled"):
                details = "扫描已取消，保留已找到的结果。" + details
            if setting_error:
                details += (" " if details else "") + setting_error
            if cache_error:
                details += (" " if details else "") + cache_error
            if details:
                self.note.set(details)
            elif self.blocker.error:
                self.note.set(self.blocker.error)
            self._reload_history()
        def failed(error):
            if not self._destroyed:
                self.scanning = False
                self.finish_scan_progress("扫描未完成")
                self.note.set(f"扫描未完成：{error}")
                self.update_counter()
        self.app.start("扫描软件弹窗规则", work, done, failed)

    def consume_scan_progress(self, data):
        """Worker updates arrive through the application's main-thread queue."""
        if self._destroyed or not self.scanning:
            return
        self.progress_text.set(data["message"])
        total = data.get("total")
        if total is None:
            if str(self.scan_progress.cget("mode")) != "indeterminate":
                self.scan_progress.configure(mode="indeterminate")
                self.scan_progress.start(12)
            self.progress_percent.set("读取中")
        else:
            self.scan_progress.stop()
            self.scan_progress.configure(mode="determinate")
            self._scan_fraction = min(100, max(0, 100*data.get("completed", 0)/total)) if total else 0
            self.progress_value.set(self._scan_fraction)
            self.progress_percent.set(f"{self._scan_fraction:.0f}%")

    def finish_scan_progress(self, message, complete=False):
        self.scan_progress.stop()
        self.scan_progress.configure(mode="determinate")
        if complete:
            self._scan_fraction = 100
        self.progress_value.set(self._scan_fraction)
        self.progress_percent.set(f"{self._scan_fraction:.0f}%")
        self.progress_text.set(message)

    def render(self):
        self.items = backend.visible_items(self.items)
        self.blocker.set_items(self.items)
        for row in self.rows.values():
            row.destroy()
        self.rows.clear()
        self.icons.clear()
        self.empty.pack_forget()
        self.expanded.intersection_update(item.key for item in self.items)
        for item in self.items:
            row = PopupRow(self.inner, self, item)
            row.pack(fill="x")
            self.rows[item.key] = row
        if not self.items:
            self.empty.configure(text="正在扫描软件行为…" if self.scanning else
                                 ("暂无可拦截对象" if self._loaded else "暂无扫描结果，请点击扫描。"))
            self.empty.pack(fill="x")
        self.update_counter()
        self.inner.update_idletasks()
        self.update_scrollregion()

    def update_counter(self):
        if self._destroyed:
            return
        total = sum(len(item.rules) for item in self.items)
        vendor = [rule for item in self.items for rule in item.rules if rule.source == 'huorong']
        noun = '个软件分组' if vendor else '款软件'
        self.heading.set("正在扫描软件行为…" if self.scanning else
                         f"发现 {len(self.items)} {noun} {total} 项广告弹窗")
        self.summary.set("告别弹窗骚扰")
        self.scan_button.set_enabled(not self.scanning)
        self.block_button.caption = "全部取消" if self.blocker.enabled else "全部拦截"
        self.block_button.set_enabled((self.blocker.enabled or any(self.blocker.rule_available(rule)
                                      for item in self.items for rule in item.rules)) and not self.scanning)
        self.app.set_nav_metric("popup", total, "项")
        for row in self.rows.values():
            row.refresh()

    def toggle_expanded(self, key):
        if key in self.expanded:
            self.expanded.discard(key)
        else:
            self.expanded.add(key)
        if key in self.rows:
            self.rows[key].refresh()
            # Off-screen canvas windows can be unmapped, so shrinking them
            # does not reliably emit <Configure>. Reconcile after layout.
            self.inner.update_idletasks()
            self.update_scrollregion()

    def _change(self, action):
        if self.scanning:
            return
        try:
            action()
        except (OSError, ValueError) as error:
            self.note.set(str(error))
        else:
            self.note.set(self.blocker.error or "")
        self.update_counter()

    def set_software(self, item, value):
        self._change(lambda: self.blocker.set_software(item, value))

    def set_rule(self, rule, value):
        self._change(lambda: self.blocker.set_rule(rule, value))

    def open_rule_options(self, rule):
        if self.scanning:
            self.note.set('正在扫描，请等扫描完成后再设置规则。')
        elif not rule.supported:
            self.note.set(f"{rule.label}：暂不支持，{rule.reason or '规则条件无法完整核对'}")
        else:
            self.set_rule(rule, True)

    def toggle_all(self):
        self._change(lambda: self.blocker.set_all(not self.blocker.enabled))

    def tick(self):
        self._tick_after = None
        if self._destroyed:
            return
        try:
            changed, error = self._poll_results.get_nowait()
        except queue.Empty:
            pass
        else:
            self._poll_worker = None
            if changed:
                self.update_counter()
                self._reload_history()
            if error and error != self._last_error:
                self.note.set(error)
            self._last_error = error
        if (self._poll_worker is None and not self.scanning
                and (self.blocker.pending or self.blocker.enabled and self.items)):
            self._poll_worker = threading.Thread(target=self._poll_background,
                                                 args=(self.blocker, self._poll_results),
                                                 name="CPlus-popup-check", daemon=True)
            self._poll_worker.start()
        self._tick_after = self.after(250, self.tick)

    @staticmethod
    def _poll_background(blocker, results):
        # No Tk calls from this worker; an unresponsive foreign window cannot
        # hold up navigation, closing the window, or hiding it to the tray.
        try:
            changed, error = blocker.poll(), blocker.error or ""
        except Exception as exception:
            changed, error = False, f"弹窗拦截未完成：{exception}"
        results.put((changed, error))

    def show_history(self):
        window, created = single_window(self, "_history_window", "拦截记录")
        if not created:
            return window
        window.configure(bg=WHITE)
        window.geometry("720x470")
        window.minsize(630, 350)
        top = tk.Frame(window, bg=WHITE)
        top.pack(fill="x", padx=22, pady=(18, 14))
        window.total_label = MetricLabel(top, text="累计为您拦截弹窗 0 次（最近30天）",
                                         bg=WHITE, fg=INK, font=(FONT, 12))
        window.total_label.pack(side="left", fill="x", expand=True)
        window.clear_button = text_button(top, "清空记录", self.clear_history)
        window.clear_button.pack(side="right")
        frame = tk.Frame(window, bg=WHITE)
        frame.pack(fill="both", expand=True, padx=22, pady=(0, 22))
        tree = ttk.Treeview(frame, columns=("source", "position", "time", "count"),
                            show="headings", selectmode="browse")
        for column, title, width in (("source", "弹窗来源", 240), ("position", "弹窗位置", 100),
                                      ("time", "拦截时间", 150), ("count", "拦截次数", 80)):
            tree.heading(column, text=title)
            tree.column(column, width=width, minwidth=55, anchor="w" if column == "source" else "center")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        window.tree = tree
        def reload_records():
            tree.delete(*tree.get_children())
            try:
                now = time.time()
                records = [record for record in self.blocker.history()
                           if float(record.get("time", 0)) + 30*86400 >= now]
                records.sort(key=lambda record: float(record.get("time", 0)), reverse=True)
                rules = {rule.id: rule for item in self.items for rule in item.rules}
                for record in records:
                    software, title = str(record.get("software", "")), str(record.get("title", ""))
                    source = software + (" · " + tr(title) if title and title != software else "")
                    rule = rules.get(record.get("rule_id"))
                    position = position_text(rule.pos, rule.source) if rule is not None else "—"
                    if rule is not None and rule.source == 'huorong':
                        source += ' · ' + tr(backend.huorong.ACTION_LABELS.get(rule.action, rule.action))
                    stamp = time.strftime("%Y/%m/%d %H:%M", time.localtime(float(record.get("time", 0))))
                    tree.insert("", "end", values=(original(source or title), position, stamp, record.get("count", 0)))
                total = sum(int(record.get("count", 0)) for record in records)
                window.total_label.configure(text=f"累计为您拦截弹窗 {total} 次（最近30天）")
                window.clear_button.configure(state="normal" if records else "disabled")
                if not records:
                    tree.insert("", "end", values=("暂无拦截记录", "", "", ""))
            except (OSError, ValueError, OverflowError) as error:
                self.note.set(f"读取拦截记录失败：{error}")
        window.reload_records = reload_records
        reload_records()
        return window

    open_history = show_history

    def _reload_history(self):
        window = self._history_window
        if window is not None and window.winfo_exists() and window.reload_records:
            window.reload_records()

    def clear_history(self):
        try:
            self.blocker.clear_history()
        except (OSError, ValueError) as error:
            self.note.set(f"清空拦截记录失败：{error}")
            return
        self._reload_history()

    def destroy(self):
        self._destroyed = True
        self.blocker.stop()
        self.scan_progress.stop()
        for attribute in ("_pending_open", "_tick_after"):
            timer = getattr(self, attribute, None)
            if timer is not None:
                self.after_cancel(timer)
                setattr(self, attribute, None)
        if self._wheel_binding:
            self._root()._unbind(("bind", "all", "<MouseWheel>"), self._wheel_binding)
            self._wheel_binding = None
        if self._history_window is not None:
            self._history_window.destroy()
        super().destroy()
        self.icons.clear()
        self.warning_image = None
        for attribute in ("heading", "summary", "note", "progress_text", "progress_percent", "progress_value"):
            setattr(self, attribute, None)

# Purpose: Popup groups, expansion, switches and history
