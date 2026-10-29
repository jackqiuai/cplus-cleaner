"""Software compression inventory, serial actions, and one history window."""
from __future__ import annotations

import base64
from dataclasses import replace
import math
from pathlib import Path
import time
from cplus_cleaner.localization import localized_tk as tk
from cplus_cleaner.localization.i18n import tr, display_name
from tkinter import font
from cplus_cleaner.localization.localized_tk import ttk

from cplus_cleaner.features.app_compression import software_compression as backend
from cplus_cleaner.ui.desktop import WHITE, INK, MUTED, LINE, FONT, PillButton, size_text
from cplus_cleaner.ui.ui_widgets import Checkbutton, MetricLabel, single_window, scroll_canvas


def error_text(errors):
    errors = list(errors or [])
    return "；".join(str(error) for error in errors[:3]) + (f"；另有{len(errors)-3}项" if len(errors) > 3 else "")


def error_summary(errors):
    """Keep the outcome from each error, omitting multiline command chatter."""
    errors = [errors] if isinstance(errors, str) else list(errors or [])
    summaries = []
    for error in errors[:3]:
        lines = [' '.join(line.split()) for line in str(error).splitlines() if line.strip()]
        if lines and lines[-1] not in summaries:
            summaries.append(lines[-1])
    text = '；'.join(summaries)
    return text if len(text) <= 240 else text[:239]+'…'


def record_time(value):
    if isinstance(value, (int, float)):
        try:
            return time.strftime("%Y/%m/%d %H:%M", time.localtime(value))
        except (OverflowError, ValueError, OSError):
            return ""
    return str(value or "")


class SoftwareRow(tk.Frame):
    HEIGHT = 67

    def __init__(self, parent, page, item):
        super().__init__(parent, bg=WHITE, height=self.HEIGHT)
        self.page, self.item = page, item
        self.grid_propagate(False)
        self.configure_columns(self)
        self.rowconfigure(0, weight=1)
        self.value = tk.BooleanVar(value=item.key in page.selected)
        self.checkbox = Checkbutton(self, variable=self.value, command=self.toggle, bg=WHITE)
        self.checkbox.grid(row=0, column=0)
        self.icon = tk.Label(self, image=page.item_icon(item) or "", bg=WHITE)
        self.icon.grid(row=0, column=1)
        labels = tk.Frame(self, bg=WHITE)
        labels.grid(row=0, column=2, sticky="ew", padx=(5, 10))
        self.name = tk.Label(labels, text=display_name(item.name), bg=WHITE, fg="#6f8d93", font=(FONT, 10), anchor="w")
        self.name.pack(fill="x")
        self.path = tk.Label(labels, text=str(item.folder), bg=WHITE, fg=MUTED, font=(FONT, 9), anchor="w")
        self.path.pack(fill="x", pady=(1, 0))
        self.before = tk.Label(self, text=size_text(item.size), bg=WHITE, fg=MUTED, font=(FONT, 9))
        self.before.grid(row=0, column=3)
        self.after = tk.Label(self, text=size_text(item.after) if item.after is not None else "--", bg=WHITE,
                              fg=MUTED, font=(FONT, 9))
        self.after.grid(row=0, column=4)
        self.action = PillButton(self, "还原" if item.compressed else "压缩",
                                 lambda: page.act_items([item], "restore" if item.compressed else "compress"),
                                 width=104, height=32)
        self.action.grid(row=0, column=5, padx=(8, 0))
        if item.blocked:
            self.action.bind('<Enter>',lambda event: page.note.set(item.blocked),add='+')
        tk.Frame(self, bg=LINE, height=1).grid(row=1, column=0, columnspan=6, sticky="ew")
        labels.bind("<Configure>", self.fit)
        self.refresh()

    @staticmethod
    def configure_columns(widget):
        for column, width in ((0, 29), (1, 36), (3, 100), (4, 100), (5, 112)):
            widget.columnconfigure(column, minsize=width)
        widget.columnconfigure(2, weight=1, minsize=95)

    def fit(self, event):
        for label, text in ((self.name, display_name(self.item.name)), (self.path, str(self.item.folder))):
            label.fit_text(text, event.width)

    def refresh(self):
        state = self.page.states.get(self.item.key, "ready")
        if self.item.blocked:
            caption = "受保护"
        elif state == "running":
            caption = "还原中…" if self.page.operation == "restore" else "压缩中…"
        elif state == "queued":
            caption = "等待处理"
        else:
            caption = "还原" if self.item.compressed else "压缩"
        self.action.caption = caption
        self.action.set_enabled(not self.page.processing and not self.item.blocked)
        self.checkbox.configure(state="normal" if not self.page.processing and self.page.selectable(self.item) else "disabled")
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
        self.value = None


class SoftwareCompressionPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=WHITE)
        self.app = app
        self.apps, self.rows, self.icons, self.selected, self.states = [], {}, {}, set(), {}
        self.results, self.errors, self.queue = [], {}, []
        self.scanning = self.queue_active = self._loaded = False
        self._pending_open = self._queue_after = self._history_window = None
        self._queue_cancelled = False
        self.operation = "compress"
        self._last_progress = 0.0
        self._progress_total = self._progress_finished = 0
        self._progress_units = self._current_progress = 0.0
        self._progress_name = ''
        self._activity_running = False
        self.heading = tk.StringVar(value="发现可压缩软件 0 款，已选择 0 款")
        self.note = tk.StringVar(value="点击“扫描”读取可压缩的软件列表。")
        self.search = tk.StringVar()
        self.select_all_value = tk.IntVar(value=0)
        header = tk.Frame(self, bg=WHITE)
        header.pack(fill="x", padx=25, pady=(25, 21))
        header.columnconfigure(1, weight=1)
        self.warning_image = app.images.get("status-warning.png")
        self.warning = tk.Label(header, image=self.warning_image or "", bg=WHITE)
        self.warning.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 12), pady=2)
        self.heading_label = MetricLabel(header, textvariable=self.heading, bg=WHITE, fg=INK, font=(FONT, 14))
        self.heading_label.grid(row=0, column=1, sticky="nw")
        self.subtitle = tk.Label(header, text="容量按实际磁盘占用统计", bg=WHITE, fg="#c1c7c8",
                                 anchor="w", font=(FONT, 9))
        self.subtitle.grid(row=1, column=1, sticky="nw", pady=(6, 0))
        actions = tk.Frame(header, bg=WHITE)
        actions.grid(row=0, column=2, rowspan=2, sticky="ne", padx=(14, 0))
        self.scan_button = PillButton(actions, "扫描", self.load, width=145)
        self.scan_button.pack(side="left", padx=(0, 10))
        self.compress_button = PillButton(actions, "压缩", self.compress_selected, width=145)
        self.compress_button.pack(side="left")

        def fit_header(event):
            width = max(140, event.width-actions.winfo_reqwidth()-self.warning.winfo_reqwidth()-35)
            self.heading_label.configure(wraplength=width, font=(FONT, 12 if width < 380 else 14))
        header.bind("<Configure>", fit_header)
        # A compact filter keeps large inventories usable without changing the
        # original row structure or selecting hidden software implicitly.
        search_frame = tk.Frame(self, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        search_frame.pack(fill="x", padx=25, pady=(0, 12))
        tk.Label(search_frame, text="⌕", bg=WHITE, fg=MUTED, font=(FONT, 14)).pack(side="left", padx=8)
        self.search_entry = tk.Entry(search_frame, textvariable=self.search, relief="flat", bd=0,
                                     bg=WHITE, fg=INK, font=(FONT, 10))
        self.search_entry.pack(fill="x", expand=True, padx=(0, 10), ipady=5)
        self.search_placeholder = tk.Label(self.search_entry, text="搜索软件", bg=WHITE, fg="#aebdc0", font=(FONT, 10), cursor="xterm")
        self.search_placeholder.place(x=0, rely=.5, anchor="w")
        self.search_placeholder.bind("<Button-1>", lambda event: self.search_entry.focus_set())
        self.search_entry.bind("<FocusIn>", lambda event: self.search_placeholder.place_forget())
        self.search_entry.bind("<FocusOut>", lambda event: self._placeholder())
        self._search_trace = self.search.trace_add("write", self._search_changed)
        self.table_header = tk.Frame(self, bg="#f2f7fa")
        self.table_header.pack(fill="x", padx=(25, 41))
        SoftwareRow.configure_columns(self.table_header)
        self.select_all_button = Checkbutton(self.table_header, variable=self.select_all_value,
                                             command=self.select_all, tristatevalue=-1, bg="#f2f7fa")
        self.select_all_button.grid(row=0, column=0, pady=5)
        for text, column, span in (("软件名称", 1, 2), ("压缩前大小", 3, 1), ("压缩后大小", 4, 1), ("操作", 5, 1)):
            tk.Label(self.table_header, text=text, bg="#f2f7fa", fg=MUTED, font=(FONT, 9),
                     anchor="w" if column == 1 else "center").grid(row=0, column=column, columnspan=span, sticky="ew")
        footer = tk.Frame(self, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        footer.pack(side="bottom", fill="x", pady=(9, 0))
        self.history_button = tk.Button(footer, text="压缩记录", command=self.open_history,
                                        bg=WHITE, activebackground=WHITE, fg=MUTED, font=(FONT, 9),
                                        bd=0, cursor="hand2", padx=15, pady=10)
        self.history_button.pack(side="right")
        tk.Label(self, textvariable=self.note, bg=WHITE, fg=MUTED, anchor="w", justify="left",
                 wraplength=700, font=(FONT, 9)).pack(side="bottom", fill="x", padx=25, pady=(7, 0))
        self.progress_frame = tk.Frame(self,bg=WHITE)
        progress_heading = tk.Frame(self.progress_frame,bg=WHITE)
        progress_heading.pack(fill='x',pady=(0,5))
        self.progress_caption = tk.Label(progress_heading,text='',bg=WHITE,fg=INK,
            font=(FONT,9),anchor='w',width=1)
        self.progress_caption.pack(side='left',fill='x',expand=True)
        self.progress_percent = tk.Label(progress_heading,text='0%',bg=WHITE,fg='#27b77d',font=(FONT,9,'bold'))
        self.progress_percent.pack(side='right',padx=(10,0))
        self.progress_value = tk.DoubleVar(value=0)
        ttk.Style(self).configure('Compression.Horizontal.TProgressbar',background='#51db9e',
            troughcolor='#edf7f2',borderwidth=0,thickness=10)
        ttk.Style(self).configure('Compression.Error.Horizontal.TProgressbar',background='#ff6269',
            troughcolor='#fff0f0',borderwidth=0,thickness=10)
        self.progress_bar = ttk.Progressbar(self.progress_frame,variable=self.progress_value,maximum=100,
            mode='determinate',style='Compression.Horizontal.TProgressbar')
        self.progress_bar.pack(fill='x')
        self.activity_bar=ttk.Progressbar(self.progress_frame,mode='indeterminate',
            style='Compression.Horizontal.TProgressbar',maximum=100)
        area = tk.Frame(self, bg=WHITE)
        self._list_area = area
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
        self.empty = tk.Label(self.inner, text="尚未扫描，请点击“扫描”。", bg=WHITE, fg=MUTED, font=(FONT, 11), pady=48)
        self.empty.pack(fill="x")
        self._wheel_binding = self.bind_all("<MouseWheel>", self.mousewheel, add="+")
        self.update_summary()

    @property
    def processing(self):
        return self.scanning or self.queue_active or self.app.busy

    @property
    def items(self):
        return self.apps

    def _placeholder(self):
        if not self.search.get() and self.focus_get() is not self.search_entry:
            self.search_placeholder.place(x=0, rely=.5, anchor="w")
        else:
            self.search_placeholder.place_forget()

    def _search_changed(self, *args):
        self._placeholder()
        self.render()

    def item_icon(self, item):
        if item.key not in self.icons and item.icon:
            try:
                image = tk.PhotoImage(master=self, data=base64.b64encode(item.icon))
                largest = max(image.width(), image.height())
                common = math.gcd(largest, 30)
                self.icons[item.key] = image.zoom(30 // common).subsample(largest // common) if largest != 30 else image
            except tk.TclError:
                self.icons[item.key] = None
        return self.icons.get(item.key) or self.app.images.navigation("compress")

    def selectable(self, item):
        return not item.blocked and not item.compressed

    def visible_apps(self):
        query = self.search.get().strip().casefold()
        return sorted((item for item in self.apps if query in item.name.casefold() or query in tr(display_name(item.name)).casefold()),
                      key=lambda item: (-item.size, item.name.casefold()))

    def render(self):
        position = self.canvas.yview()[0]
        for row in self.rows.values():
            row.destroy()
        self.rows = {}
        visible = self.visible_apps()
        if visible:
            self.empty.pack_forget()
        else:
            self.empty.configure(text="正在扫描软件…" if self.scanning else "没有找到匹配的软件" if self.search.get().strip() else "暂无可压缩软件")
            self.empty.pack(fill="x")
        for item in visible:
            row = SoftwareRow(self.inner, self, item)
            row.pack(fill="x")
            self.rows[item.key] = row
        self.canvas.yview_moveto(position)
        self.update_summary()

    def update_summary(self):
        self.selected.intersection_update(item.key for item in self.apps if self.selectable(item))
        self.heading.set(f"发现可压缩软件 {len(self.apps)} 款，已选择 {len(self.selected)} 款"
                         if self._loaded or self.scanning or self.apps else "软件压缩")
        if hasattr(self.app, "set_nav_metric") and (self._loaded or self.scanning or self.apps):
            self.app.set_nav_metric("compress", sum(item.size for item in self.apps))
        visible = [item for item in self.visible_apps() if self.selectable(item)]
        checked = sum(item.key in self.selected for item in visible)
        self.select_all_value.set(1 if visible and checked == len(visible) else -1 if checked else 0)
        available = not self.processing
        self.select_all_button.configure(state="normal" if available and visible else "disabled")
        self.scan_button.set_enabled(available)
        self.compress_button.set_enabled(available and bool(self.selected))
        for row in self.rows.values():
            row.refresh()
        self._refresh_history_controls()

    def select_all(self):
        if not self.processing:
            visible = [item for item in self.visible_apps() if self.selectable(item)]
            if self.select_all_value.get():
                self.selected.update(item.key for item in visible)
            else:
                self.selected.difference_update(item.key for item in visible)
        self.update_summary()

    def mousewheel(self, event):
        target = self.winfo_containing(event.x_root, event.y_root)
        while target is not None:
            if target == self.canvas:
                return scroll_canvas(self.canvas, self.inner, event.delta)
            target = getattr(target, "master", None)

    def on_open(self):
        if self._pending_open is not None:
            self.after_cancel(self._pending_open)
            self._pending_open = None
        self.update_summary()
        if not self.scanning and not self._loaded and not self.apps:
            self.heading.set("软件压缩")
            self.empty.configure(text="尚未扫描，请点击“扫描”。")

    def load(self):
        if self._pending_open is not None:
            self.after_cancel(self._pending_open)
            self._pending_open = None
        if self.processing:
            return
        self.scanning = True
        self.progress_frame.pack_forget()
        self.apps, self.selected, self.states, self.results, self.errors = [], set(), {}, [], {}
        self.icons.clear()
        self.search.set("")
        self.render()
        self.note.set("正在扫描可压缩软件…")

        def work():
            return backend.scan(cancel=self.app.cancel, progress=self.report_progress)

        def done(result):
            self.scanning = False
            self._loaded = True
            self.apps = [item for item in result.get("items", []) if not item.blocked]
            self.states = {item.key: "ready" for item in self.apps}
            self.render()
            detail = error_text(result.get("errors"))
            self.note.set(("扫描已取消，保留已找到的软件。" if result.get("cancelled") else "扫描完成。") + (" " + detail if detail else ""))

        def failed(error):
            self.scanning = False
            self.note.set(f"扫描未完成：{error}")
            self.update_summary()

        self.app.start("扫描可压缩软件", work, done, failed)

    scan = load

    def report_progress(self, data):
        now = time.monotonic()
        value = data.get('progress') if isinstance(data,dict) else None
        if now-self._last_progress < .15 and value != 100:
            return
        self._last_progress = now
        message = str(data.get("message", "正在处理…")) if isinstance(data, dict) else str(data)
        if isinstance(data, dict) and isinstance(data.get("progress"), (int, float)):
            message += f"（{max(0, min(100, data['progress'])):.0f}%）"
        if isinstance(data,dict) and data.get('current_file'):
            if isinstance(data.get('elapsed'),(int,float)):
                message += f" · 已用时 {int(data['elapsed'])} 秒"
            message += f" · 当前文件：{data['current_file']}"
        self.app.report_progress(message)
        self.app.events.put(("batch", (self._apply_progress, (data,message))))

    def _show_progress(self):
        if not self.progress_frame.winfo_manager():
            self.progress_frame.pack(side='bottom',fill='x',padx=25,pady=(10,3),before=self._list_area)
        window=self._history_window
        if window is not None and window.winfo_exists() and not window.progress_frame.winfo_manager():
            window.progress_frame.pack(side='bottom',fill='x',padx=20,pady=(3,15),before=window.records_area)

    def _set_progress(self,value,caption=None,*,error=False):
        value = max(0.0,min(100.0,float(value)))
        self.progress_value.set(value)
        color = '#ff6269' if error else '#27b77d'
        self.progress_percent.configure(text=f'{value:.0f}%',fg=color)
        self.progress_caption.configure(fg='#ff6269' if error else INK)
        style='Compression.Error.Horizontal.TProgressbar' if error else 'Compression.Horizontal.TProgressbar'
        self.progress_bar.configure(style=style)
        if caption is not None:
            self.progress_caption.configure(text=caption)
        window=self._history_window
        if window is not None and window.winfo_exists():
            window.progress_caption.configure(text=self.progress_caption.cget('text'),fg=self.progress_caption.cget('fg'))
            window.progress_percent.configure(text=f'{value:.0f}%',fg=color)
            window.progress_bar.configure(style=style)
            window.result_note.configure(fg='#ff6269' if error else MUTED)

    def _set_activity(self,active):
        active=bool(active)
        if active != self._activity_running:
            self._activity_running=active
            if active:
                self.activity_bar.pack(fill='x',pady=(4,0))
                self.activity_bar.start(25)
            else:
                self.activity_bar.stop()
                self.activity_bar.pack_forget()
        window=self._history_window
        if window is not None and window.winfo_exists():
            if active and not window.activity_bar.winfo_manager():
                window.activity_bar.pack(fill='x',pady=(4,0),before=window.result_note)
                window.activity_bar.start(25)
            elif not active:
                window.activity_bar.stop()
                window.activity_bar.pack_forget()

    def _current_caption(self,detail=''):
        verb = '还原' if self.operation=='restore' else '压缩'
        return f'正在{verb} {self._progress_finished+1}/{self._progress_total} 款 · {self._progress_name}'+detail

    def _apply_progress(self,payload):
        data,message = payload
        self.note.set(message)
        if not self.queue_active or not self._progress_total or not isinstance(data,dict):
            return
        self._set_activity(data.get('busy',False))
        value=data.get('progress')
        completed,total=data.get('completed'),data.get('total')
        if not isinstance(value,(int,float)) and isinstance(completed,(int,float)) and isinstance(total,(int,float)) and total>0:
            value=100*completed/total
        if isinstance(value,(int,float)) and math.isfinite(value):
            self._current_progress=max(self._current_progress,max(0,min(100,value)))
        overall=100*(self._progress_units+min(.99,self._current_progress/100))/self._progress_total
        detail=f' · {completed}/{total} 个文件' if isinstance(completed,int) and isinstance(total,int) else ''
        if isinstance(data.get('elapsed'),(int,float)):
            detail += f" · 已用时 {int(data['elapsed'])} 秒"
        if data.get('current_file'):
            detail += f" · {Path(data['current_file']).name}"
            if isinstance(data.get('current_file_size'),(int,float)):
                detail += f" ({size_text(data['current_file_size'])})"
        self._set_progress(min(99,overall),self._current_caption(detail))

    def compress_selected(self):
        self.act_items([item for item in self.apps if item.key in self.selected], "compress")

    def act_items(self, items, operation="compress"):
        if self.processing or operation not in {"compress", "restore"}:
            return
        inventory = {item.key: item for item in self.apps}
        requested, seen = [], set()
        for item in items:
            if isinstance(item, dict):
                if operation == "restore" and item.get("can_restore") and not item.get("restored") and not item.get("superseded"):
                    key = ("record", item.get("id"))
                    if key not in seen:
                        requested.append(item)
                        seen.add(key)
                continue
            current = inventory.get(item.key)
            if current is None or current.key in seen or current.blocked:
                continue
            if operation == "compress" and not self.selectable(current):
                continue
            if operation == "restore" and not current.compressed:
                continue
            requested.append(current)
            seen.add(current.key)
        if not requested:
            return
        self.app.cancel.clear()
        self.queue, self.operation = requested, operation
        self.queue_active, self._queue_cancelled = True, False
        self.results, self.errors = [], {}
        self._progress_total,self._progress_finished=len(requested),0
        self._progress_units=self._current_progress=0.0
        self._last_progress=0.0
        self._set_activity(False)
        self._show_progress()
        self._set_progress(0)
        for item in requested:
            if not isinstance(item, dict):
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
        self._queue_cancelled = self._queue_cancelled or self.app.cancel.is_set()
        if self.app.busy:
            self._queue_after = self.after(150, self._advance_queue)
            return
        if self._queue_cancelled:
            for waiting in self.queue:
                if not isinstance(waiting, dict):
                    self.states[waiting.key] = "cancelled"
            self.queue = []
        if not self.queue:
            self.queue_active = False
            self._set_activity(False)
            counts = {status: sum(result.get("status") == status for result in self.results)
                      for status in ("success", "partial", "failed", "cancelled")}
            verb = "还原" if self.operation == "restore" else "压缩"
            complete=not self._queue_cancelled and counts['success']==self._progress_total
            state='完成' if complete else '已取消' if self._queue_cancelled else '部分完成' if counts['success'] or counts['partial'] else '未完成'
            self._set_progress(min(99,self.progress_value.get()) if self._queue_cancelled else 100,
                f'{verb}{state} · 完成 {counts["success"]}/{self._progress_total} 款',
                error=not complete and not self._queue_cancelled)
            self.note.set(("任务已取消；" if self._queue_cancelled else "") +
                          f"完成 {counts['success']} 款，部分完成 {counts['partial']} 款，失败 {counts['failed']} 款。" +
                          (" " + error_text(list(self.errors.values())) if self.errors else ""))
            self.update_summary()
            self._reload_history()
            return
        item = self.queue.pop(0)
        name = item.get("name", "软件") if isinstance(item, dict) else item.name
        key = str(item.get("id", "record")) if isinstance(item, dict) else item.key
        self.states[key] = "running"
        self._progress_name=name
        self._current_progress=0.0
        self._last_progress=0.0
        self._set_progress(min(99,100*self._progress_units/max(1,self._progress_total)),self._current_caption())
        self.note.set(f"正在{'还原' if self.operation == 'restore' else '压缩'} {name}…")
        self.update_summary()
        operation = self.operation

        def work():
            try:
                action = backend.restore if operation == "restore" else backend.compress
                return action(item, cancel=self.app.cancel, progress=self.report_progress)
            except Exception as error:
                return {"status": "failed", "errors": [str(error)]}

        def done(result):
            self._set_activity(False)
            status = result.get("status", "failed")
            if status not in {"success", "partial", "failed", "cancelled"}:
                status = "failed"
            self.states[key] = status
            self.results.append(dict(result, status=status))
            self._progress_finished+=1
            interrupted = self.app.cancel.is_set() or bool(result.get('cancelled')) or status == 'cancelled'
            self._progress_units+=min(.99,self._current_progress/100) if interrupted else 1.0
            self._set_progress(min(99,100*self._progress_units/max(1,self._progress_total)))
            detail = error_text(result.get("errors"))
            if detail:
                self.errors[key] = f"{name}：{detail}"
            self._queue_cancelled = self._queue_cancelled or self.app.cancel.is_set() or bool(result.get("cancelled")) or status == "cancelled"
            updated = result.get("item")
            if updated is not None:
                self.apps = [updated if old.key == updated.key else old for old in self.apps]
            elif status in {"success", "partial"}:
                for index, old in enumerate(self.apps):
                    matches = old.key == key if not isinstance(item, dict) else str(old.folder).casefold() == str(item.get("install_path", "")).casefold()
                    if matches:
                        after = result.get("after", old.after)
                        self.apps[index] = replace(old, after=after, compressed=operation == "compress")
            self.render()
            self._reload_history()
            self._schedule_next()

        self.app.start(f"{'还原' if operation == 'restore' else '压缩'} {name}", work, done,
                       lambda error: done({"status": "failed", "errors": [error]}))

    def open_history(self):
        window, created = single_window(self, "_history_window", "压缩记录")
        if not created:
            return window
        window.geometry("850x480")
        window.minsize(650, 320)
        window.configure(bg=WHITE)
        window.transient(self.winfo_toplevel())
        tk.Label(window, text="软件压缩记录", bg=WHITE, fg=INK, font=(FONT, 14), anchor="w").pack(fill="x", padx=20, pady=(18, 12))
        window.progress_frame=tk.Frame(window,bg=WHITE)
        progress_heading=tk.Frame(window.progress_frame,bg=WHITE)
        progress_heading.pack(fill='x',pady=(0,5))
        window.progress_caption=tk.Label(progress_heading,text=self.progress_caption.cget('text'),
            bg=WHITE,fg=INK,font=(FONT,9),anchor='w',width=1)
        window.progress_caption.pack(side='left',fill='x',expand=True)
        window.progress_percent=tk.Label(progress_heading,text=self.progress_percent.cget('text'),
            bg=WHITE,fg='#27b77d',font=(FONT,9,'bold'))
        window.progress_percent.pack(side='right')
        window.progress_bar=ttk.Progressbar(window.progress_frame,variable=self.progress_value,maximum=100,
            mode='determinate',style='Compression.Horizontal.TProgressbar')
        window.progress_bar.pack(fill='x')
        window.activity_bar=ttk.Progressbar(window.progress_frame,mode='indeterminate',
            style='Compression.Horizontal.TProgressbar',maximum=100)
        window.result_note=tk.Label(window.progress_frame,textvariable=self.note,bg=WHITE,
            fg=MUTED,font=(FONT,9),anchor='w',justify='left',wraplength=780)
        window.result_note.pack(fill='x',pady=(6,0))
        window.progress_frame.bind('<Configure>',lambda event:window.result_note.configure(wraplength=max(200,event.width)))
        container = tk.Frame(window, bg=WHITE)
        window.records_area=container
        container.pack(fill="both", expand=True, padx=20, pady=(0, 18))
        if self.queue_active:self._show_progress()
        self._set_activity(self._activity_running)
        canvas = tk.Canvas(container, bg=WHITE, bd=0, highlightthickness=0)
        scroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas, bg=WHITE)
        inner_id = canvas.create_window(0, 0, anchor="nw", window=inner)
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(inner_id, width=event.width))
        inner.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        window.record_buttons = []

        def reload_records(records=None):
            for widget in inner.winfo_children():
                widget.destroy()
            window.record_buttons = []
            try:
                records = list(backend.history()) if records is None else records
            except Exception as error:
                tk.Label(inner, text=f"读取记录失败：{error}", bg=WHITE, fg=MUTED, wraplength=580).pack(fill="x", pady=20)
                return
            if not records:
                tk.Label(inner, text="暂无压缩记录", bg=WHITE, fg=MUTED, font=(FONT, 11)).pack(fill="x", pady=48)
            for record in records:
                row = tk.Frame(inner, bg=WHITE)
                row.pack(fill="x", pady=3)
                row.columnconfigure(0, weight=1)
                name = str(record.get("name", "软件"))
                text = name + "  " + record_time(record.get("time", ""))
                tk.Label(row, text=text, bg=WHITE, fg=INK, anchor="w", font=(FONT, 10)).grid(row=0, column=0, sticky="ew", pady=(8, 2))
                before, after = record.get("old_size", 0), record.get("new_size", 0)
                if record.get('schema', 0) >= 2:
                    detail = f"压缩前占用 {size_text(before)}  ·  压缩后占用 {size_text(after)}  ·  本次节省 {size_text(record.get('saved_bytes', 0))}"
                else:
                    # Legacy records compared logical size with physical usage;
                    # they cannot establish how much this operation saved.
                    detail = '旧记录：未记录操作前的实际占用'
                if record.get('current_size') is not None:
                    detail += f"  ·  当前占用 {size_text(record['current_size'])}"
                if record.get('refresh_error'):
                    detail += '  ·  当前占用读取失败'
                tk.Label(row, text=detail, bg=WHITE, fg=MUTED, anchor="w", font=(FONT, 9)).grid(row=1, column=0, sticky="ew", pady=(0, 8))
                caption = ("记录已更新" if record.get("superseded") else "已还原" if record.get("restored")
                           else "继续还原" if record.get('status') in {'partial', 'failed', 'cancelled'} and record.get('can_restore')
                           else "还原")
                button = PillButton(row, caption,
                                    lambda record=record: self.act_items([record], "restore"), width=104, height=32)
                button.grid(row=0, column=1, rowspan=2, padx=(10, 0))
                window.record_buttons.append((button, record))
                detail_error=error_summary(record.get('last_errors')) or error_summary(self.errors.get(str(record.get('id')),''))
                if detail_error:
                    error_label=tk.Label(row,text=f'未完成原因：{detail_error}',bg=WHITE,fg='#ff6269',
                        anchor='w',font=(FONT,9),wraplength=0,height=1,width=1)
                    error_label.grid(row=2,column=0,columnspan=2,sticky='ew',pady=(0,8))
                    row.bind('<Configure>',lambda event,label=error_label,text=f'未完成原因：{detail_error}':
                             label.fit_text(text,max(100,event.width-12)))
                tk.Frame(row, bg=LINE, height=1).grid(row=3, column=0, columnspan=2, sticky="ew")
            self._refresh_history_controls()
        window.reload_records = reload_records
        reload_records()
        if not self.processing:
            def refreshed(records):
                if window.winfo_exists():
                    reload_records(records)
                self.update_summary()
            def failed(error):
                self.note.set(f'读取记录失败：{error}')
                self.update_summary()
            self.app.start('读取软件压缩记录', lambda: backend.history(refresh=True, cancel=self.app.cancel),
                           refreshed, failed)
            self.update_summary()
        return window

    def _reload_history(self):
        window = self._history_window
        if window is not None and window.winfo_exists() and window.reload_records:
            window.reload_records()

    def _refresh_history_controls(self):
        window = self._history_window
        if window is not None and window.winfo_exists():
            for button, record in getattr(window, "record_buttons", []):
                button.set_enabled(not self.processing and bool(record.get("can_restore")) and not record.get("restored") and not record.get("superseded"))

    def destroy(self):
        self._set_activity(False)
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
        if self._history_window is not None:
            self._history_window.destroy()
        super().destroy()
        self.icons.clear()
        self.warning_image = None
        for attribute in ("heading", "note", "search", "select_all_value", "progress_value"):
            setattr(self, attribute, None)

# Purpose: App Compression page and progress
