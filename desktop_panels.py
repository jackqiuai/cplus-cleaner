"""Shared operations, preferences and feedback window contents."""
from __future__ import annotations

import localized_tk as tk
from tkinter import font as tkfont
from localized_tk import ttk
import webbrowser

import feedback
import preferences
import i18n
from ui_widgets import Checkbutton

WHITE, INK, MUTED, MINT, LINE = "#ffffff", "#263b40", "#85969b", "#46d6a0", "#edf2f2"
FONT = "Microsoft YaHei UI"


def label(parent, text, size=10, color=INK, **kwargs):
    return tk.Label(parent, text=text, bg=WHITE, fg=color, font=(FONT, size), **kwargs)


class Panel(tk.Frame):
    def __init__(self, parent, app, title, description):
        super().__init__(parent, bg=WHITE)
        self.app = app
        self.header = tk.Frame(self, bg=WHITE)
        self.header.pack(fill="x", padx=34, pady=(26, 10))
        window = parent.winfo_toplevel()
        popup = window is not app
        self.return_button = ttk.Button(self.header, text="关闭" if popup else "返回",
                                        command=window.destroy if popup else lambda: app.navigate(app.previous_tool))
        self.return_button.pack(side="right")
        label(self.header, title, 21).pack(side="left")
        label(self, description, 10, MUTED, anchor="w", justify="left", wraplength=720).pack(fill="x", padx=35, pady=(0, 20))
        tk.Frame(self, bg=LINE, height=1).pack(fill="x", padx=35)
        self.body = tk.Frame(self, bg=WHITE)
        self.body.pack(fill="both", expand=True, padx=35, pady=20)


class OperationsPanel(Panel):
    ACTIONS = {"delete": "文件清理", "chat_cleanup": "聊天清理", "privacy_cleanup": "隐私清理", "software_uninstall": "软件卸载", "uninstall_residue": "卸载残留清理", "software_compress": "软件压缩", "software_restore": "压缩还原", "scheduled_cleanup": "定时清理", "feedback": "本地反馈",
               "bigfile_delete": "删除大文件", "bigfile_move": "大文件搬家",
               "settings": "修改设置", "empty_recycle_bin": "清空回收站", "hibernate_off": "关闭休眠",
               "hibernate_on": "开启休眠", "pagefile_settings": "虚拟内存设置",
               "analyze_component_store": "组件分析", "cleanup_component_store": "组件清理"}

    def __init__(self, parent, app):
        super().__init__(parent, app, "操作记录", "查看最近 500 条清理、系统操作及定时任务记录。")
        toolbar = tk.Frame(self.body, bg=WHITE)
        toolbar.pack(fill="x", pady=(0, 12))
        self.filter = tk.StringVar(value="全部")
        choice = ttk.Combobox(toolbar, values=("全部", "成功", "失败"), textvariable=self.filter, state="readonly", width=10)
        choice.pack(side="left")
        choice.bind("<<ComboboxSelected>>", lambda event: self.render())
        buttons = tk.Frame(toolbar, bg=WHITE)
        buttons.pack(side="right")
        self.delete_button = ttk.Button(buttons, text="删除", command=self.delete_selected, state="disabled")
        self.delete_button.pack(side="left")
        self.clear_button = ttk.Button(buttons, text="全部清空", command=self.clear_all)
        self.clear_button.pack(side="left", padx=(8, 0))
        self.summary = tk.StringVar()
        tk.Label(toolbar, textvariable=self.summary, bg=WHITE, fg=MUTED, font=(FONT, 9)).pack(side="left", padx=15)
        table = tk.Frame(self.body, bg=WHITE)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("time", "action", "result", "detail"), show="headings", selectmode="extended", height=10)
        for name, title, width in (("time", "时间", 160), ("action", "操作", 110), ("result", "结果", 65), ("detail", "内容", 360)):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, minwidth=60, stretch=name == "detail")
        scrollbar = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.selection_changed)
        self.records, self.visible, self.issues = [], [], []

    def refresh(self):
        self.records, self.issues = preferences.read_operations()
        self.render()

    def render(self):
        self.tree.delete(*self.tree.get_children())
        selected = self.filter.get()
        self.visible = [record for record in self.records if selected == "全部" or
                        (record.get("ok") is True if selected == "成功" else record.get("ok") is False)]
        for index, record in enumerate(self.visible):
            ok = record.get("ok")
            detail = self.describe(record)
            self.tree.insert("", "end", iid=str(index), values=(record.get("time", ""),
                self.ACTIONS.get(record.get("action"), record.get("action", "操作")),
                "成功" if ok is True else "失败" if ok is False else "记录", detail))
        self.summary.set(f"{len(self.visible)} 条记录" + (" · 部分记录无法读取" if self.issues else ""))
        self.selection_changed()

    @staticmethod
    def describe(record):
        detail = str(record.get("summary") or record.get("path") or record.get("error") or record.get("message") or "")
        for status, caption in (("success", "已完成"), ("failed", "失败"), ("partial", "部分完成"),
                                ("cancelled", "已取消"), ("started", "已启动")):
            if detail.endswith("："+status):
                detail = detail[:-len(status)] + caption
                break
        if record.get("action") in {"software_compress", "software_restore"}:
            from desktop import size_text
            if isinstance(record.get("count"), int):
                detail += f" · 处理 {record['count']} 个文件"
            before, after = record.get("bytes_before"), record.get("bytes_after")
            if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                detail += f" · {size_text(before)} → {size_text(after)}"
        error = record.get("error")
        if error and str(error) != detail:
            detail += "；" + str(error)
        return detail

    def selection_changed(self, event=None):
        self.delete_button.configure(state="normal" if self.tree.selection() else "disabled")

    def delete_selected(self):
        if self.app.busy:
            self.summary.set("任务运行中，请完成后再删除记录")
            return
        selection = self.tree.selection()
        if not selection:
            return
        try:
            result = preferences.delete_operations([self.visible[int(index)] for index in selection])
        except (OSError, ValueError) as error:
            self.summary.set(f"删除未完成：{error}")
            return
        self.refresh()
        self.summary.set(self.summary.get()+f" · 已删除 {result['deleted']} 条"+
                         (" · "+"；".join(result['errors']) if result['errors'] else ""))

    def clear_all(self):
        if self.app.busy:
            self.summary.set("任务运行中，请完成后再清空记录")
            return
        result = preferences.clear_operations()
        self.refresh()
        self.summary.set("清空未完成 · "+"；".join(result['errors']) if result['errors']
                         else "0 条记录 · 已全部清空")


class SettingsPanel(Panel):
    def __init__(self, parent, app):
        from desktop import PillButton
        super().__init__(parent, app, "设置", "按你的习惯安排清理时间与窗口关闭行为。")
        self.return_button.destroy()
        self.autostart, self.scheduled, self.tray = (tk.BooleanVar() for _ in range(3))
        self.cleanup_time = tk.StringVar()
        self.next_run = tk.StringVar()
        self.message = tk.StringVar()
        language_row = tk.Frame(self.body, bg=WHITE)
        language_row.pack(fill='x', pady=(0, 8))
        label(language_row, '语言', 11).pack(side='left')
        self.language = tk.StringVar()
        self.language_combo = ttk.Combobox(
            language_row, values=(i18n.original('中文'), i18n.original('English')),
            textvariable=self.language, state='readonly', width=15)
        self.language_combo.pack(side='left', padx=15)
        label(self.body, '选择界面语言；保存后立即生效。', 9, MUTED,
              wraplength=680, justify='left').pack(anchor='w', pady=(0, 15))
        self.check("开机自启动", self.autostart, "登录 Windows 后启动软件，并驻留系统托盘。")
        self.check("定时清理", self.scheduled, "每天按指定时间清理 C盘的默认常规类型；包含直接删除操作。")
        time_row = tk.Frame(self.body, bg=WHITE)
        time_row.pack(fill="x", padx=26, pady=(0, 5))
        label(time_row, "每天").pack(side="left")
        self.time_entry = ttk.Entry(time_row, textvariable=self.cleanup_time, width=9)
        self.time_entry.pack(side="left", padx=12)
        label(time_row, "24小时制，例如 20:00", 9, MUTED).pack(side="left")
        label(self.body, "软件运行或驻留托盘时执行；退出软件后暂停。忙碌时延后，手动停止后当天不重试。", 9, MUTED,
              wraplength=640, justify="left").pack(anchor="w", padx=26, pady=(0, 8))
        tk.Label(self.body, textvariable=self.next_run, bg=WHITE, fg="#229f78", font=(FONT, 9)).pack(anchor="w", padx=26, pady=(0, 22))
        self.check("点击关闭时隐藏到系统托盘", self.tray, "关闭主窗口后继续运行；点击托盘图标打开，右键可退出软件。")
        footer = tk.Frame(self.body, bg=WHITE)
        footer.pack(fill="x", pady=(24, 10))
        self.button_font = tkfont.Font(self, font=ttk.Style(self).lookup("TButton", "font") or (FONT, 10))
        self.button_font.configure(weight="bold")
        button_font = self.button_font
        self.save_button = PillButton(footer, "保存设置", self.save, width=150, font=button_font)
        self.save_button.pack(side="left")
        self.close_button = self.return_button = PillButton(
            footer, "关闭", parent.winfo_toplevel().destroy, width=150, font=button_font)
        self.close_button.pack(side="left", padx=(12, 0))
        tk.Label(self.body, textvariable=self.message, bg=WHITE, fg=MUTED, font=(FONT, 10), wraplength=680, justify="left").pack(anchor="w", pady=8)
        self.scheduled.trace_add("write", lambda *args: self.time_entry.configure(state="normal" if self.scheduled.get() else "disabled"))

    def check(self, title, value, description):
        Checkbutton(self.body, text=title, variable=value, bg=WHITE, fg=INK, activebackground=WHITE,
                       font=(FONT, 11), selectcolor="#ecfaf5", bd=0, highlightthickness=0).pack(anchor="w")
        label(self.body, description, 9, MUTED, wraplength=640, justify="left").pack(anchor="w", padx=26, pady=(3, 20))

    def refresh(self):
        values = self.app.settings
        self.autostart.set(values["autostart"])
        self.scheduled.set(values["scheduled_cleanup"])
        self.tray.set(values["close_to_tray"])
        self.cleanup_time.set(values["cleanup_time"])
        self.language.set('中文' if (values.get('language') or i18n.detect_language()) == 'zh' else 'English')
        next_run = self.app.scheduler.next_run
        self.next_run.set(f"下次清理：{next_run:%Y-%m-%d %H:%M}" if next_run else "")
        self.message.set("")

    def save(self):
        values = dict(self.app.settings, autostart=self.autostart.get(), scheduled_cleanup=self.scheduled.get(),
                      close_to_tray=self.tray.get(), cleanup_time=self.cleanup_time.get().strip(),
                      language='zh' if self.language.get() == '中文' else 'en')
        try:
            self.app.apply_settings(values)
        except (OSError, ValueError) as exc:
            self.message.set(f"设置未保存：{exc}")
            return
        self.refresh()
        self.message.set("设置已保存，立即生效。")


class FeedbackPanel(Panel):
    def __init__(self, parent, app):
        from desktop import PillButton
        super().__init__(parent, app, "问题反馈", "通过 GitHub 报告问题、提出建议，并跟踪处理进度。")
        label(self.body, f"CPlus Cleaner v{preferences.VERSION}", 12).pack(anchor="w", pady=(0, 12))
        self.link_buttons = {}
        for key, title, description in (
            ("bug", "报告问题", "遇到功能异常？请描述问题和复现步骤。"),
            ("feature", "功能建议", "分享使用场景和你希望增加或改进的功能。"),
            ("issues", "查看已有反馈", "查看是否已有相同问题，以及处理进度。"),
        ):
            row = tk.Frame(self.body, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
            row.pack(fill="x", pady=(0, 8))
            button = PillButton(row, title, lambda key=key: self.open_link(key), width=150)
            button.pack(side="right", padx=14, pady=13)
            text = tk.Frame(row, bg=WHITE)
            text.pack(side="left", fill="both", expand=True, padx=(16, 8), pady=11)
            label(text, title, 11).pack(anchor="w")
            label(text, description, 9, MUTED).pack(anchor="w", pady=(3, 0))
            button.set_enabled(bool(feedback.FEEDBACK_URLS[key]))
            self.link_buttons[key] = button
        environment = tk.Frame(self.body, bg=WHITE)
        environment.pack(fill="x", pady=(8, 12))
        self.copy_button = PillButton(environment, "复制环境信息", self.copy_environment, width=150)
        self.copy_button.pack(side="right", padx=(14, 0), pady=7)
        self.environment = tk.StringVar(value=feedback.environment_info())
        tk.Label(environment, textvariable=self.environment, bg=WHITE, fg=MUTED,
                 font=(FONT, 9), justify="left", anchor="w").pack(side="left", fill="x", expand=True)
        label(self.body, "反馈将公开显示，请勿提交个人隐私信息。提交需登录 GitHub。", 9, MUTED,
              wraplength=650, justify="left").pack(anchor="w")
        self.message = tk.StringVar(value="GitHub 链接待配置，暂时无法在线提交。")
        tk.Label(self.body, textvariable=self.message, bg=WHITE, fg="#229f78", font=(FONT, 9),
                 wraplength=650, justify="left", anchor="w").pack(fill="x", pady=(8, 0))

    def open_link(self, key):
        url = feedback.FEEDBACK_URLS.get(key, "").strip()
        if not url:
            self.message.set("GitHub 链接待配置，暂时无法在线提交。")
            return
        try:
            if webbrowser.open_new_tab(url):
                self.message.set("已在浏览器打开 GitHub，请在网页中完成操作。")
            else:
                self.message.set("无法打开浏览器，请稍后重试。")
        except (OSError, webbrowser.Error):
            self.message.set("无法打开浏览器，请稍后重试。")

    def copy_environment(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(i18n.tr(self.environment.get()))
            self.message.set("已复制环境信息，可粘贴到 GitHub 反馈表单。")
        except tk.TclError:
            self.message.set("无法复制环境信息，请稍后重试。")
